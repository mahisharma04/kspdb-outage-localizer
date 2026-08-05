"""Fault localization — the core algorithm.

Input: a NetworkGraph (radial trees per DT) and a per-pole *state* map with
values LIVE / DARK / UNKNOWN. Output: a small set of located Incidents.

The physics we exploit
----------------------
The LT side is radial. A span failure darkens everything electrically
downstream of it and nothing upstream, so the observable signature of a fault
is a *boundary*: the last live pole (upstream) and the first dark pole
(downstream). The fault is on the edge between them. Sensors report on nodes;
we infer the failed edge as the frontier between the live region and the dark
region.

What the algorithm does, per DT
-------------------------------
1. If no device-equipped pole under the DT is live while some are dark, the
   fault is the DT / HT fuse itself (DT-equipment fault) — one incident, not
   one-per-pole.
2. Otherwise it finds every "dark head": a dark pole whose parent is not dark
   (its parent is live, unknown, or the DT). Each dark head is the frontier of
   one span fault. All poles below a head are its single incident — this is how
   dozens of dark poles collapse into one ticket. Two independent faults on the
   same line produce two heads -> two incidents (never merged, never split).
3. Sensor-lie guard: a dark pole with a *live* descendant is physically
   impossible as a line fault (power reaches the descendant *through* it). That
   is a lying sensor / broken lamp point, not an outage — classified as a
   low-priority 'sensor' incident, never a normal outage ticket.
4. Boundary refinement for missing devices: from a dark head we walk upstream
   through device-less / unknown poles until the first live pole (or the DT).
   If the span passes through poles we can't observe, we report a *range*, not
   a point, and say so.
5. Feeder rollup: if every DT on a feeder is a full DT-fault, that is one
   feeder incident, not N DT incidents.

Confidence is computed from concrete, reported factors (topology source,
whether a dying 'power_lost' packet corroborated the silence, device coverage
of the boundary, geometric ambiguity). Every factor is emitted as a
human-readable reason string for the operator.

Complexity: O(P) per detection pass over the affected DTs' poles (each pole is
visited a constant number of times); topology inference is a one-time O(n^2)
Prim per inferred DT at build. Known failure cases are documented in
ARCHITECTURE.md.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .config import settings
from .geo import midpoint
from .topology import NetworkGraph

LIVE = "live"
DARK = "dark"
UNKNOWN = "unknown"

# Below this inferred-topology quality we refuse to claim a specific span and
# fall back to a DT-area answer (honest about what we don't know).
DT_AREA_THRESHOLD = 0.55


@dataclass
class Incident:
    incident_key: str
    fault_type: str            # span | dt | feeder | sensor
    localization_kind: str     # span_point | span_range | dt_area | dt_equipment | feeder_area | sensor_point
    feeder_id: str | None
    dt_id: str | None
    lat: float
    lon: float
    span_from_pole: str | None = None
    span_to_pole: str | None = None
    pincode: str | None = None
    ward: str | None = None
    affected_poles: list[str] = field(default_factory=list)
    poles_affected: int = 0
    households_affected: int = 0
    confidence: float = 0.0
    confidence_band: str = "low"
    topology_source: str = "none"
    reasons: list[str] = field(default_factory=list)
    first_symptom_ts: dt.datetime | None = None


def _band(c: float) -> str:
    return "high" if c >= 0.8 else "medium" if c >= 0.55 else "low"


def localize(
    graph: NetworkGraph,
    states: dict[str, str],
    *,
    corroborated: set[str] | None = None,
    symptom_ts: dict[str, dt.datetime] | None = None,
) -> list[Incident]:
    """Return the located incidents implied by ``states``.

    ``corroborated`` is the set of poles for which an explicit ``power_lost``
    packet was received (vs. darkness inferred only from silence). It raises
    confidence. ``symptom_ts`` maps pole -> first-symptom time.
    """
    corroborated = corroborated or set()
    symptom_ts = symptom_ts or {}

    def st(pid: str) -> str:
        if pid in states:
            return states[pid]
        return LIVE if graph.poles[pid].has_device else UNKNOWN

    # Group dark poles by DT.
    dark_by_dt: dict[str, list[str]] = {}
    for pid, s in states.items():
        if s == DARK and pid in graph.poles:
            dark_by_dt.setdefault(graph.poles[pid].dt_id, []).append(pid)

    incidents: list[Incident] = []
    full_dt_by_feeder: dict[str, list[str]] = {}
    dt_incident_index: dict[str, int] = {}  # dt_id -> index into incidents (for feeder rollup)

    for dt_id, _darks in dark_by_dt.items():
        dtn = graph.dts[dt_id]
        device_poles = [p for p in dtn.pole_ids if graph.poles[p].has_device]
        any_live_device = any(st(p) == LIVE for p in device_poles)
        any_dark = any(st(p) == DARK for p in dtn.pole_ids)

        # --- (1) whole-DT fault -----------------------------------------
        if device_poles and not any_live_device and any_dark:
            inc = _dt_equipment_incident(graph, dtn, st, corroborated, symptom_ts)
            dt_incident_index[dt_id] = len(incidents)
            incidents.append(inc)
            full_dt_by_feeder.setdefault(dtn.feeder_id, []).append(dt_id)
            continue

        # --- (2) span boundaries ----------------------------------------
        for h in _dark_heads(graph, dtn, st):
            region = graph.descendants(h)
            live_desc = [x for x in region if graph.poles[x].has_device and st(x) == LIVE]
            if live_desc:
                # (3) sensor lie: darkness with live power downstream.
                incidents.append(_sensor_incident(graph, dtn, h, symptom_ts))
                continue
            incidents.append(_span_incident(graph, dtn, h, region, st, corroborated, symptom_ts))

    # --- (5) feeder rollup ---------------------------------------------
    incidents = _feeder_rollup(graph, incidents, full_dt_by_feeder, dt_incident_index, symptom_ts)
    return incidents


# --------------------------------------------------------------------------
# boundary discovery
# --------------------------------------------------------------------------
def _dark_heads(graph: NetworkGraph, dtn, st) -> list[str]:
    """Dark poles whose parent is not itself dark => frontier of a fault."""
    heads = []
    for pid in dtn.pole_ids:
        if st(pid) != DARK:
            continue
        par = graph.parent.get(pid)
        if par is None or st(par) != DARK:
            heads.append(pid)
    return heads


def _walk_up_boundary(graph: NetworkGraph, head: str, st):
    """From a dark head, walk upstream through unknown/device-less poles until
    the first live pole or the DT. Returns (upstream_pole_or_None, passed_unknown)."""
    passed_unknown = 0
    cur = graph.parent.get(head)
    while cur is not None and st(cur) == UNKNOWN:
        passed_unknown += 1
        cur = graph.parent.get(cur)
    return cur, passed_unknown  # cur is a live pole or None (=DT)


# --------------------------------------------------------------------------
# incident builders
# --------------------------------------------------------------------------
def _centroid(graph: NetworkGraph, pole_ids: list[str]) -> tuple[float, float]:
    if not pole_ids:
        return 0.0, 0.0
    la = sum(graph.poles[p].lat for p in pole_ids) / len(pole_ids)
    lo = sum(graph.poles[p].lon for p in pole_ids) / len(pole_ids)
    return la, lo


def _pin_ward(graph: NetworkGraph, pole_ids: list[str]):
    for p in pole_ids:
        if graph.poles[p].pincode:
            return graph.poles[p].pincode, graph.poles[p].ward
    ward = graph.poles[pole_ids[0]].ward if pole_ids else None
    return None, ward


def _first_ts(symptom_ts, pole_ids):
    ts = [symptom_ts[p] for p in pole_ids if p in symptom_ts]
    return min(ts) if ts else None


def _span_incident(graph, dtn, head, region, st, corroborated, symptom_ts) -> Incident:
    up, passed_unknown = _walk_up_boundary(graph, head, st)
    up_lat, up_lon = (dtn.lat, dtn.lon) if up is None else (graph.poles[up].lat, graph.poles[up].lon)
    head_p = graph.poles[head]
    lat, lon = midpoint(up_lat, up_lon, head_p.lat, head_p.lon)

    dark_region = [x for x in region if st(x) in (DARK, UNKNOWN)]
    poles_affected = len(region)
    households = round(dtn.households_served * poles_affected / max(1, len(dtn.pole_ids)))

    # Precision & confidence.
    reasons: list[str] = []
    q = dtn.inference_quality
    head_has_dev = head_p.has_device
    up_has_dev = up is not None and graph.poles[up].has_device

    if dtn.topology_source == "recorded":
        conf = 0.9
        reasons.append("Pole ordering for this DT is recorded in the registry, so the live/dark boundary maps directly to a physical span.")
        kind = "span_point"
    else:
        # inferred
        if q < DT_AREA_THRESHOLD:
            # Degrade: don't pretend to know the span.
            la, lo = _centroid(graph, [x for x in region if st(x) == DARK] or region)
            pin, ward = _pin_ward(graph, region)
            conf = 0.55
            reasons.append(f"Pole ordering for this DT was never digitized and its geometry is ambiguous (inference quality {q:.2f}); localizing to DT area rather than a single span.")
            if corroborated & set(region):
                conf += 0.05
                reasons.append("At least one device sent a 'power_lost' packet, confirming a real outage (not silence).")
            conf = min(conf, 0.7)
            return Incident(
                incident_key=f"{dtn.id}:span:{head}",
                fault_type="span", localization_kind="dt_area",
                feeder_id=dtn.feeder_id, dt_id=dtn.id, lat=la, lon=lo,
                span_from_pole=None, span_to_pole=None, pincode=pin, ward=ward,
                affected_poles=dark_region, poles_affected=poles_affected,
                households_affected=households, confidence=round(conf, 2),
                confidence_band=_band(conf), topology_source="inferred",
                reasons=reasons, first_symptom_ts=_first_ts(symptom_ts, region),
            )
        conf = 0.5 + 0.4 * q
        reasons.append(f"Pole ordering inferred geometrically (MST from GPS); inference quality {q:.2f} for this DT.")
        kind = "span_point"

    # corroboration
    if corroborated & set(region):
        conf += 0.05
        reasons.append("A 'power_lost' packet was received from the dark region — real outage, not a silent sensor.")
    else:
        conf -= 0.08
        reasons.append("No 'power_lost' packet arrived; darkness inferred from missed heartbeats (weaker signal).")

    # device coverage of the boundary
    if passed_unknown > 0 or not head_has_dev or not up_has_dev:
        kind = "span_range"
        conf -= 0.1
        reasons.append(f"The boundary crosses {passed_unknown} pole(s) with no telemetry; the true break is somewhere in that range, so the span is reported as a range, not a point.")
    else:
        reasons.append("Both boundary poles report telemetry, so the failed span is pinned between two adjacent poles.")

    if head in graph.ambiguous or (up in graph.ambiguous if up else False):
        conf -= 0.1
        reasons.append("A boundary pole sits in a geometrically ambiguous cluster; the inferred parent could be wrong.")

    n_dark = sum(1 for x in region if st(x) == DARK)
    if n_dark >= 3:
        conf += 0.03
        reasons.append(f"{n_dark} downstream poles corroborate the same dark region.")

    conf = max(0.05, min(0.98, conf))
    pin, ward = _pin_ward(graph, [head] + region)
    return Incident(
        incident_key=f"{dtn.id}:span:{head}",
        fault_type="span", localization_kind=kind,
        feeder_id=dtn.feeder_id, dt_id=dtn.id, lat=lat, lon=lon,
        span_from_pole=(up or dtn.id), span_to_pole=head,
        pincode=pin, ward=ward, affected_poles=dark_region,
        poles_affected=poles_affected, households_affected=households,
        confidence=round(conf, 2), confidence_band=_band(conf),
        topology_source=dtn.topology_source, reasons=reasons,
        first_symptom_ts=_first_ts(symptom_ts, region),
    )


def _dt_equipment_incident(graph, dtn, st, corroborated, symptom_ts) -> Incident:
    region = list(dtn.pole_ids)
    reasons = [
        "Every pole under this DT is dark with no live pole beneath it — the signature of a DT / HT-fuse failure, not a line span.",
    ]
    conf = 0.85
    if corroborated & set(region):
        conf += 0.05
        reasons.append("Corroborated by at least one 'power_lost' packet.")
    pin, ward = _pin_ward(graph, region)
    return Incident(
        incident_key=f"{dtn.id}:dt",
        fault_type="dt", localization_kind="dt_equipment",
        feeder_id=dtn.feeder_id, dt_id=dtn.id, lat=dtn.lat, lon=dtn.lon,
        span_from_pole=None, span_to_pole=None, pincode=pin, ward=ward,
        affected_poles=[x for x in region if st(x) != LIVE],
        poles_affected=len(region), households_affected=dtn.households_served,
        confidence=round(min(conf, 0.95), 2), confidence_band=_band(conf),
        topology_source=dtn.topology_source, reasons=reasons,
        first_symptom_ts=_first_ts(symptom_ts, region),
    )


def _sensor_incident(graph, dtn, pole, symptom_ts) -> Incident:
    p = graph.poles[pole]
    return Incident(
        incident_key=f"{pole}:sensor",
        fault_type="sensor", localization_kind="sensor_point",
        feeder_id=dtn.feeder_id, dt_id=dtn.id, lat=p.lat, lon=p.lon,
        span_from_pole=None, span_to_pole=pole, pincode=p.pincode, ward=p.ward,
        affected_poles=[pole], poles_affected=1, households_affected=0,
        confidence=0.8, confidence_band="high", topology_source=dtn.topology_source,
        reasons=[
            "This pole reports dark while poles downstream of it are still live. "
            "A line fault here is physically impossible (power reaches the "
            "downstream poles through this one), so this is a failed lamp point "
            "or a lying sensor — flagged for maintenance, not dispatched as an outage.",
        ],
        first_symptom_ts=_first_ts(symptom_ts, [pole]),
    )


def _feeder_rollup(graph, incidents, full_dt_by_feeder, dt_incident_index, symptom_ts) -> list[Incident]:
    to_remove: set[int] = set()
    rolled: list[Incident] = []
    for feeder_id, dt_ids in full_dt_by_feeder.items():
        total_dts = len(graph.dt_of_feeder.get(feeder_id, []))
        if total_dts > 1 and len(set(dt_ids)) == total_dts:
            # every DT on the feeder is fully dark -> feeder fault
            for did in dt_ids:
                to_remove.add(dt_incident_index[did])
            dt_lats = [graph.dts[d].lat for d in dt_ids]
            dt_lons = [graph.dts[d].lon for d in dt_ids]
            households = sum(graph.dts[d].households_served for d in dt_ids)
            poles = sum(len(graph.dts[d].pole_ids) for d in dt_ids)
            pin, ward = _pin_ward(graph, [p for d in dt_ids for p in graph.dts[d].pole_ids])
            rolled.append(Incident(
                incident_key=f"{feeder_id}:feeder",
                fault_type="feeder", localization_kind="feeder_area",
                feeder_id=feeder_id, dt_id=None,
                lat=sum(dt_lats) / len(dt_lats), lon=sum(dt_lons) / len(dt_lons),
                pincode=pin, ward=ward, affected_poles=[],
                poles_affected=poles, households_affected=households,
                confidence=0.9, confidence_band="high", topology_source="n/a",
                reasons=[
                    f"All {total_dts} distribution transformers on feeder {feeder_id} went dark together — the failure is on the 11 kV feeder itself, upstream of every DT.",
                ],
                first_symptom_ts=None,
            ))
    kept = [inc for i, inc in enumerate(incidents) if i not in to_remove]
    return kept + rolled

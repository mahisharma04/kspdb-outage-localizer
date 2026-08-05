"""In-memory liveness store — the hot path.

This is the source of truth for "is each pole live right now?" at runtime. It is
kept in memory (not the DB) because the steady-state telemetry rate (~39 msg/s,
bursting to thousands) is dominated by 15-minute heartbeats that carry no state
change; touching the DB on each would be wasteful. State *transitions* are
mirrored to the DeviceState table by the detector for restart recovery.

Two jobs live here:

1. ``apply(msg)`` — ingest one telemetry message with correct de-duplication and
   ordering. Within a device, ``seq`` is monotonic and the device clock is
   internally consistent, so we order by device timestamp and use ``seq`` to drop
   exact duplicates. ``boot`` resets ``seq`` to 0, so a pre-boot straggler (a
   6-hour-late ``power_lost`` retry) is identified by an *older timestamp* than
   the current state and dropped. Cross-device skew (±90 s) never matters here
   because we never compare timestamps across devices.

2. ``snapshot(graph)`` — turn raw per-pole state into the LIVE / DARK / UNKNOWN
   map the localizer consumes, applying the key physical rule: **darkness
   propagates downstream**. A pole downstream of a confirmed-dark pole is dark
   too, even if its last (pre-fault) heartbeat said "live" — we must not let a
   stale heartbeat mask a real outage. The one exception is a descendant that
   reported live *after* the upstream pole went dark: that is the genuine
   "dark pole with a live child" case (a lying sensor), and we preserve it so
   the localizer can flag it.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field

from .config import settings
from .localization import DARK, LIVE, UNKNOWN
from .topology import NetworkGraph


@dataclass
class PoleRuntime:
    energized: bool | None = None
    last_live_ts: dt.datetime | None = None      # device ts of latest energized=True
    dark_ts: dt.datetime | None = None           # device ts of latest energized=False
    applied_ts: dt.datetime | None = None        # device ts of last applied state change
    had_power_lost: bool = False                 # explicit power_lost (vs inferred)
    was_dark: bool = False                        # ever confirmed dark (sticky; for restoration)
    server_seen: float = 0.0                     # wall-clock epoch of last message
    last_seq: int | None = None
    boot_epoch: int = 0
    battery_mv: int | None = None
    rssi: int | None = None
    fw: str | None = None


@dataclass
class ApplyResult:
    accepted: bool
    transition: bool = False          # did energized state change?
    pole_id: str | None = None


class LivenessStore:
    def __init__(self) -> None:
        self.rt: dict[str, PoleRuntime] = {}

    # ---- initialization -------------------------------------------------
    def prime_all_live(self, graph: NetworkGraph) -> None:
        """Start every device-equipped pole as energized (fresh)."""
        now = time.time()
        for pid, p in graph.poles.items():
            if p.has_device:
                self.rt[pid] = PoleRuntime(energized=True, server_seen=now, fw=p.fw,
                                           last_live_ts=_now_utc())

    def force_dark(self, pole_id: str, ts: dt.datetime | None = None) -> None:
        rt = self.rt.setdefault(pole_id, PoleRuntime())
        rt.energized = False
        rt.dark_ts = ts or _now_utc()
        rt.applied_ts = rt.dark_ts
        rt.was_dark = True

    # ---- ingest ---------------------------------------------------------
    def apply(self, msg: dict) -> ApplyResult:
        pid = msg["pole_id"]
        rt = self.rt.setdefault(pid, PoleRuntime())
        seq = msg.get("seq")
        event = msg.get("event", "heartbeat")
        ts = _parse_ts(msg.get("ts"))
        now = time.time()

        # exact duplicate within the same boot epoch
        if event != "boot" and seq is not None and rt.last_seq == seq:
            return ApplyResult(accepted=False, pole_id=pid)

        energized = _energized_from(event, msg.get("energized"))

        # boot resets the sequence counter and always applies
        if event == "boot":
            rt.boot_epoch += 1
            rt.last_seq = seq
            rt.server_seen = now
            return self._set_state(rt, pid, True, ts, is_power_lost=False, force=True)

        # ordering guard: drop clearly stale messages (older than current state
        # beyond the skew tolerance) — handles out-of-order + very late retries.
        if rt.applied_ts is not None and ts is not None:
            age = (rt.applied_ts - ts).total_seconds()
            if age > settings.CLOCK_SKEW_S:
                # stale for state purposes; still refresh liveness bookkeeping
                rt.server_seen = now
                if seq is not None:
                    rt.last_seq = max(rt.last_seq or 0, seq)
                return ApplyResult(accepted=True, transition=False, pole_id=pid)

        rt.server_seen = now
        if seq is not None:
            rt.last_seq = seq
        rt.battery_mv = msg.get("battery_mv", rt.battery_mv)
        rt.rssi = msg.get("rssi", rt.rssi)
        if energized is None:
            return ApplyResult(accepted=True, transition=False, pole_id=pid)
        return self._set_state(rt, pid, energized, ts,
                               is_power_lost=(event == "power_lost"))

    def _set_state(self, rt, pid, energized, ts, *, is_power_lost, force=False) -> ApplyResult:
        prev = rt.energized
        rt.energized = energized
        rt.applied_ts = ts or _now_utc()
        if energized:
            rt.last_live_ts = ts or _now_utc()
            rt.had_power_lost = False
            rt.dark_ts = None
        else:
            rt.dark_ts = ts or _now_utc()
            rt.had_power_lost = rt.had_power_lost or is_power_lost
            rt.was_dark = True
        return ApplyResult(accepted=True, transition=(prev != energized), pole_id=pid)

    # ---- snapshot for the localizer ------------------------------------
    def snapshot(self, graph: NetworkGraph):
        """Return (states, corroborated, symptom_ts) for the localizer."""
        now = time.time()
        sil = settings.SILENCE_TIMEOUT_S

        # Only DTs with at least one non-live device pole are interesting.
        affected: set[str] = set()
        for pid, rt in self.rt.items():
            if pid not in graph.poles:
                continue
            if rt.energized is False or (
                rt.energized is True and (now - rt.server_seen) > sil
            ):
                affected.add(graph.poles[pid].dt_id)

        states: dict[str, str] = {}
        corroborated: set[str] = set()
        symptom_ts: dict[str, dt.datetime] = {}

        for dt_id in affected:
            dtn = graph.dts[dt_id]
            raw: dict[str, tuple[str, dt.datetime | None]] = {}
            for pid in dtn.pole_ids:
                p = graph.poles[pid]
                rt = self.rt.get(pid)
                if not p.has_device or rt is None or rt.energized is None:
                    raw[pid] = ("unknown", None)
                elif rt.energized is False:
                    raw[pid] = ("dark", rt.dark_ts)
                elif (now - rt.server_seen) <= sil:
                    raw[pid] = ("live", rt.last_live_ts)
                else:
                    raw[pid] = ("silent", None)  # quiet beyond the timeout

            # DFS from DT roots, propagating darkness downstream.
            stack = [(r, None) for r in dtn.roots]
            while stack:
                pid, anc_dark_ts = stack.pop()
                kind, kts = raw[pid]
                new_anc = anc_dark_ts
                if kind == "dark":
                    states[pid] = DARK
                    if kts is not None:
                        symptom_ts[pid] = kts
                    if (self.rt.get(pid) or PoleRuntime()).had_power_lost:
                        corroborated.add(pid)
                    new_anc = kts
                elif anc_dark_ts is not None:
                    # downstream of a confirmed-dark pole
                    if kind == "live" and kts is not None and kts > anc_dark_ts:
                        states[pid] = LIVE          # genuinely live after the fault
                        new_anc = None
                    else:
                        states[pid] = DARK          # stale/silent under a dark parent
                        symptom_ts.setdefault(pid, anc_dark_ts)
                        new_anc = anc_dark_ts
                else:
                    if kind == "live":
                        states[pid] = LIVE
                    else:
                        # silent/unknown with no dark ancestor => ambiguous
                        # (dead modem / no device). Do not raise an outage.
                        states[pid] = UNKNOWN
                    new_anc = None
                for c in graph.children.get(pid, ()):
                    stack.append((c, new_anc))

        return states, corroborated, symptom_ts


# --------------------------------------------------------------------------
def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_ts(v) -> dt.datetime | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    try:
        s = str(v).replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _energized_from(event: str, energized) -> bool | None:
    if event == "power_lost":
        return False
    if event in ("power_restored", "boot"):
        return True
    if event == "heartbeat":
        return bool(energized) if energized is not None else True
    return energized if isinstance(energized, bool) else None

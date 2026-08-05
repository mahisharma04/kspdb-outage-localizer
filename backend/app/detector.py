"""Detector — orchestrates snapshot -> localize -> tickets.

Runs a *debounced* detection pass: telemetry ingestion just updates the
in-memory liveness store and pokes the detector; the detector coalesces bursts
into at most one localization pass every few seconds. Each pass:

1. snapshots liveness into LIVE/DARK/UNKNOWN,
2. localizes into a small set of incidents,
3. reconciles incidents against open tickets by incident_key (create / update,
   never one-per-pole),
4. applies scheduled-outage suppression (quiet 'planned' tickets) and escalates
   planned tickets that outlast their window,
5. auto-verifies and closes tickets whose poles have measurably come back.

State-changing events are broadcast to SSE subscribers so the console updates
live without polling.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from . import scheduled, tickets
from .ai import generate_briefing
from .config import settings
from .db import SessionLocal
from .geocode import PinResolver
from .localization import localize
from .models import DeviceState, Ticket
from .state import LivenessStore
from .topology import NetworkGraph


class Detector:
    def __init__(self, broadcaster=None) -> None:
        self.graph: NetworkGraph | None = None
        self.store = LivenessStore()
        self.pins: PinResolver | None = None
        self.broadcaster = broadcaster
        self._lock = asyncio.Lock()
        self._ticket_counter = 0

    # ---- lifecycle ------------------------------------------------------
    def load(self) -> None:
        """Build the graph and recover liveness after a (re)start."""
        with SessionLocal() as s:
            self.graph = NetworkGraph.from_db(s)
            self.pins = PinResolver(self.graph)
            self.store.prime_all_live(self.graph)
            # Overlay any persisted transitions (dark poles survive a restart).
            for ds in s.execute(select(DeviceState)).scalars():
                if ds.pole_id in self.store.rt and ds.energized is not None:
                    rt = self.store.rt[ds.pole_id]
                    rt.energized = ds.energized
                    rt.dark_ts = ds.device_ts
                    rt.last_seq = ds.last_seq
                    rt.boot_epoch = ds.boot_epoch
            n = s.execute(select(func.count()).select_from(Ticket)).scalar() or 0
            self._ticket_counter = n

    def _next_id(self) -> str:
        self._ticket_counter += 1
        return f"T-{self._ticket_counter:05d}"

    # ---- detection pass -------------------------------------------------
    async def run(self) -> list[dict]:
        async with self._lock:
            return await asyncio.to_thread(self._run_sync)

    def _run_sync(self) -> list[dict]:
        assert self.graph is not None
        states, corroborated, symptom_ts = self.store.snapshot(self.graph)
        incidents = localize(self.graph, states, corroborated=corroborated, symptom_ts=symptom_ts)

        events: list[dict] = []
        with SessionLocal() as s:
            active = scheduled.active_outages(s)
            open_tickets = {
                t.incident_key: t
                for t in s.execute(
                    select(Ticket).where(Ticket.status.in_(tuple(tickets.OPEN)))
                ).scalars()
            }
            seen: set[str] = set()

            for inc in incidents:
                seen.add(inc.incident_key)
                so = scheduled.match(inc, active)
                t = open_tickets.get(inc.incident_key)
                if t is None:
                    t = self._create(s, inc, so)
                    events.append({"type": "ticket_created", "ticket": tickets.serialize(t)})
                else:
                    changed = self._update(s, t, inc)
                    if t.status == "planned" and so is None:
                        t.status = "detected"
                        tickets.add_event(s, t, "escalated",
                                          "Planned window elapsed but poles still dark — treating as a real fault.")
                        changed = True
                    if changed:
                        events.append({"type": "ticket_updated", "ticket": tickets.serialize(t)})

            # Restoration / closure for every open ticket.
            for key, t in open_tickets.items():
                if tickets.auto_verify_if_restored(s, t, self.store):
                    events.append({"type": "ticket_closed", "ticket": tickets.serialize(t)})

            s.commit()
        return events

    # ---- ticket upsert --------------------------------------------------
    def _fill_pin(self, inc):
        pin, filled = inc.pincode, False
        if not pin and self.pins is not None:
            pin, filled = self.pins.nearest(inc.lat, inc.lon)
        return pin, filled

    def _create(self, s, inc, so) -> Ticket:
        pin, filled = self._fill_pin(inc)
        reasons = list(inc.reasons)
        if filled:
            reasons.append("PIN code approximated from the nearest surveyed pole (missing in registry).")
        summary, src = generate_briefing({
            **inc.__dict__, "pincode": pin,
        })
        status = "sensor_flagged" if inc.fault_type == "sensor" else ("planned" if so else "detected")
        t = Ticket(
            id=self._next_id(), incident_key=inc.incident_key,
            status=status,
            fault_type=inc.fault_type, localization_kind=inc.localization_kind,
            feeder_id=inc.feeder_id, dt_id=inc.dt_id,
            span_from_pole=inc.span_from_pole, span_to_pole=inc.span_to_pole,
            lat=inc.lat, lon=inc.lon, pincode=pin, ward=inc.ward,
            poles_affected=inc.poles_affected, households_affected=inc.households_affected,
            confidence=inc.confidence, confidence_band=inc.confidence_band,
            topology_source=inc.topology_source, reasons=reasons,
            affected_poles=inc.affected_poles, planned_match=(so.id if so else None),
            ai_summary=summary, first_symptom_ts=inc.first_symptom_ts,
        )
        s.add(t)
        s.flush()
        detail = "Localized fault detected." if not so else \
            f"Matches planned outage {so.id} ({so.reason}); suppressed unless it outlasts the window."
        tickets.add_event(s, t, "detected", detail)
        tickets.add_event(s, t, "briefing", f"[{src}] {summary}")
        return t

    def _update(self, s, t: Ticket, inc) -> bool:
        changed = False
        # keep the strongest confidence / freshest counts
        for attr in ("confidence", "confidence_band", "poles_affected",
                     "households_affected", "lat", "lon", "localization_kind",
                     "span_from_pole", "span_to_pole"):
            new = getattr(inc, attr)
            if getattr(t, attr) != new:
                setattr(t, attr, new)
                changed = True
        if inc.affected_poles and t.affected_poles != inc.affected_poles:
            t.affected_poles = inc.affected_poles
            changed = True
        prog = tickets.restoration_progress(self.store, t.affected_poles or [])
        if abs((t.restoration_progress or 0) - prog) > 0.001:
            t.restoration_progress = round(prog, 2)
            changed = True
        return changed

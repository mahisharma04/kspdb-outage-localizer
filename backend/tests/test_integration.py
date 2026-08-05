"""End-to-end: simulator -> ingest -> detect -> ticket -> repair -> auto-verify.

These mirror the reviewer's self-check list in 03-deliverables-and-submission.md.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Ticket


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _active(det):
    with SessionLocal() as s:
        return s.execute(
            select(Ticket).where(Ticket.status.in_(
                ("detected", "acknowledged", "assigned", "resolved_unverified")))
        ).scalars().all()


def test_span_fault_produces_exactly_one_located_ticket(system):
    det, sim = system

    async def go():
        info = await sim.inject_span(deliver_prob=1.0)   # all packets arrive
        await det.run()
        return info

    info = _run(go())
    tickets = _active(det)
    assert len(tickets) == 1
    t = tickets[0]
    assert t.fault_type == "span"
    assert t.incident_key == info["incident_key"]
    assert t.lat and t.lon and t.pincode          # drive-to coords + PIN present
    assert t.ai_summary                           # dispatch briefing generated


def test_three_simultaneous_faults_give_three_tickets(system):
    det, sim = system
    dts = [d for d, ps in sim.dt_poles.items() if len(ps) >= 8][:3]

    async def go():
        for d in dts:
            await sim.inject_span(dt_id=d, deliver_prob=1.0)
        await det.run()

    _run(go())
    assert len(_active(det)) == 3


def test_dead_sensor_does_not_create_an_outage_ticket(system):
    det, sim = system

    async def go():
        await sim.inject_dead_sensor()
        await det.run()

    _run(go())
    outages = [t for t in _active(det) if t.fault_type != "sensor"]
    assert outages == []                          # no outage raised
    with SessionLocal() as s:
        sensors = s.execute(select(Ticket).where(Ticket.fault_type == "sensor")).scalars().all()
    assert len(sensors) == 1                       # flagged as a sensor fault instead


def test_scheduled_outage_is_suppressed_not_ticketed(system):
    det, sim = system
    dt_id = next(iter(sim.dt_poles))

    async def go():
        await sim.add_scheduled_outage("dt", dt_id, minutes=60, darken=True, deliver_prob=1.0)
        await det.run()

    _run(go())
    assert _active(det) == []                       # nothing in the active list
    with SessionLocal() as s:
        planned = s.execute(select(Ticket).where(Ticket.status == "planned")).scalars().all()
    assert len(planned) == 1                        # tracked quietly as planned


def test_repair_auto_verifies_from_telemetry(system):
    det, sim = system

    async def go():
        await sim.inject_span(deliver_prob=1.0)
        await det.run()
        assert len(_active(det)) == 1
        await sim.repair()                          # poles come back to life
        await det.run()

    _run(go())
    assert _active(det) == []                        # closed automatically
    with SessionLocal() as s:
        closed = s.execute(select(Ticket).where(Ticket.status == "closed")).scalars().all()
    assert len(closed) == 1
    assert closed[0].verified_at is not None


def test_marking_resolved_while_dark_is_rejected(system):
    det, sim = system
    from app import tickets as tk

    async def go():
        await sim.inject_span(deliver_prob=1.0)
        await det.run()
        t = _active(det)[0]
        with SessionLocal() as s:
            row = s.get(Ticket, t.id)
            res = tk.mark_resolved(s, row, det.store, note="crew says fixed")
            s.commit()
        return res

    res = _run(go())
    assert res["accepted"] is False                  # system pushed back
    active = _active(det)
    assert len(active) == 1 and active[0].status == "resolved_unverified"

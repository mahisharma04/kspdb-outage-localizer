"""Ticket lifecycle.

    detected -> acknowledged -> assigned -> resolved -> verified -> closed

The rule that matters: **restoration is verified from telemetry, not from a
button.** A crew marking a ticket "resolved" while the poles are still dark does
NOT close it — it goes to `resolved_unverified` and the system pushes back. When
the affected poles actually come back to life (measured), the system moves the
ticket to `verified` and `closed` on its own, even if nobody clicked anything.
"""
from __future__ import annotations

import datetime as dt

from .config import settings
from .models import Ticket, TicketEvent

OPEN = {"detected", "acknowledged", "assigned", "resolved", "resolved_unverified", "planned", "sensor_flagged"}
CLOSED = {"verified", "closed"}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def add_event(session, ticket: Ticket, kind: str, detail: str = "") -> None:
    session.add(TicketEvent(ticket_id=ticket.id, kind=kind, detail=detail, ts=_now()))


def restoration_progress(store, affected_poles: list[str]) -> float:
    """Fraction of the poles we actually SAW go dark that are now energized.

    Measured only over poles confirmed dark (``was_dark``) — not poles whose
    dying-gasp packet was simply lost — so a brand-new fault reads 0%, and a
    genuine restoration reads high as the poles report back."""
    confirmed = [p for p in affected_poles if p in store.rt and store.rt[p].was_dark]
    if not confirmed:
        return 0.0
    live = sum(1 for p in confirmed if store.rt[p].energized is True)
    return live / len(confirmed)


def acknowledge(session, ticket: Ticket, note: str | None = None) -> None:
    if ticket.status == "detected":
        ticket.status = "acknowledged"
        ticket.acknowledged_at = _now()
        add_event(session, ticket, "acknowledged", note or "Operator acknowledged.")


def assign(session, ticket: Ticket, crew: str | None = None, note: str | None = None) -> None:
    if ticket.status in ("detected", "acknowledged"):
        ticket.status = "assigned"
        ticket.assigned_at = _now()
        add_event(session, ticket, "assigned", f"Crew {crew or 'unnamed'} assigned. {note or ''}".strip())


def mark_resolved(session, ticket: Ticket, store, note: str | None = None) -> dict:
    """A crew claims the fault is fixed. Only telemetry can actually close it."""
    ticket.crew_note = note
    prog = restoration_progress(store, ticket.affected_poles or [])
    ticket.restoration_progress = round(prog, 2)
    if prog >= settings.RESTORE_FRACTION:
        _verify_and_close(session, ticket, source="crew-claim confirmed by telemetry")
        return {"accepted": True, "status": ticket.status,
                "message": "Restoration confirmed by telemetry. Ticket closed."}
    ticket.status = "resolved_unverified"
    add_event(session, ticket, "resolved_rejected",
              f"Crew marked resolved but only {prog:.0%} of poles are live — kept open. {note or ''}".strip())
    return {"accepted": False, "status": ticket.status, "restoration_progress": ticket.restoration_progress,
            "message": "Cannot close the ticket; not all poles are live."}


def auto_verify_if_restored(session, ticket: Ticket, store) -> bool:
    """Detector hook: close the ticket from telemetry alone when poles return."""
    if ticket.status in CLOSED or ticket.status == "planned":
        return False
    prog = restoration_progress(store, ticket.affected_poles or [])
    ticket.restoration_progress = round(prog, 2)
    if prog >= settings.RESTORE_FRACTION:
        _verify_and_close(session, ticket, source="telemetry (auto)")
        return True
    return False


def _verify_and_close(session, ticket: Ticket, *, source: str) -> None:
    now = _now()
    ticket.resolved_at = ticket.resolved_at or now
    ticket.verified_at = now
    ticket.status = "verified"
    add_event(session, ticket, "verified", f"Restoration verified from {source}.")
    ticket.closed_at = now
    ticket.status = "closed"
    add_event(session, ticket, "closed", "Ticket auto-closed after verified restoration.")


def serialize(ticket: Ticket) -> dict:
    return {
        "id": ticket.id,
        "incident_key": ticket.incident_key,
        "status": ticket.status,
        "fault_type": ticket.fault_type,
        "localization_kind": ticket.localization_kind,
        "feeder_id": ticket.feeder_id,
        "dt_id": ticket.dt_id,
        "span_from_pole": ticket.span_from_pole,
        "span_to_pole": ticket.span_to_pole,
        "lat": ticket.lat,
        "lon": ticket.lon,
        "pincode": ticket.pincode,
        "ward": ticket.ward,
        "poles_affected": ticket.poles_affected,
        "households_affected": ticket.households_affected,
        "confidence": ticket.confidence,
        "confidence_band": ticket.confidence_band,
        "topology_source": ticket.topology_source,
        "reasons": ticket.reasons or [],
        "affected_poles": ticket.affected_poles or [],
        "planned_match": ticket.planned_match,
        "ai_summary": ticket.ai_summary,
        "restoration_progress": ticket.restoration_progress,
        "crew_note": ticket.crew_note,
        "first_symptom_ts": _iso(ticket.first_symptom_ts),
        "detected_at": _iso(ticket.detected_at),
        "acknowledged_at": _iso(ticket.acknowledged_at),
        "assigned_at": _iso(ticket.assigned_at),
        "resolved_at": _iso(ticket.resolved_at),
        "verified_at": _iso(ticket.verified_at),
        "closed_at": _iso(ticket.closed_at),
        "updated_at": _iso(ticket.updated_at),
        "events": [
            {"ts": _iso(e.ts), "kind": e.kind, "detail": e.detail} for e in ticket.events
        ],
    }


def _iso(v: dt.datetime | None) -> str | None:
    if v is None:
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.timezone.utc)
    return v.isoformat()

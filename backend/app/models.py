"""Persistent schema.

Design note: the *registry* (feeders / DTs / poles) is static and seeded once.
The *volatile* liveness of each pole lives primarily in an in-memory store
(see state.py) for the hot path; we persist a compact DeviceState row only on
state transitions (not on every 15-min heartbeat), so DB write volume tracks
real events rather than telemetry volume. Tickets and their audit trail are
fully persisted.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Feeder(Base):
    __tablename__ = "feeders"
    id: Mapped[str] = mapped_column(String, primary_key=True)          # F-07-03
    substation_id: Mapped[str] = mapped_column(String, index=True)


class DistributionTransformer(Base):
    __tablename__ = "dts"
    id: Mapped[str] = mapped_column(String, primary_key=True)          # D-0112
    feeder_id: Mapped[str] = mapped_column(String, ForeignKey("feeders.id"), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    capacity_kva: Mapped[int] = mapped_column(Integer)
    households_served: Mapped[int] = mapped_column(Integer)
    # True when the export recorded pole ordering for this DT (~40% of DTs).
    topology_known: Mapped[bool] = mapped_column(Boolean, default=False)


class Pole(Base):
    __tablename__ = "poles"
    id: Mapped[str] = mapped_column(String, primary_key=True)          # P-024431
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    feeder_id: Mapped[str] = mapped_column(String, index=True)
    dt_id: Mapped[str] = mapped_column(String, ForeignKey("dts.id"), index=True)
    # Exported registry values. NULL for the ~60% of DTs whose pole ordering
    # was never digitized. The localizer may ONLY use these.
    seq_on_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_pole_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Ground truth for the simulator only (never read by the localizer). This
    # models the real physical wiring so injected faults darken the correct
    # downstream poles even where the export omitted the ordering.
    true_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    true_parent_pole_id: Mapped[str | None] = mapped_column(String, nullable=True)
    pole_type: Mapped[str] = mapped_column(String, default="LT-9m-PCC")
    ward: Mapped[str | None] = mapped_column(String, nullable=True)
    pincode: Mapped[str | None] = mapped_column(String, nullable=True)
    device_id: Mapped[str | None] = mapped_column(String, nullable=True)
    fw: Mapped[str | None] = mapped_column(String, nullable=True)      # firmware line

    @property
    def has_device(self) -> bool:
        return bool(self.device_id)


class DeviceState(Base):
    """Compact last-known state per pole. Written on transitions."""
    __tablename__ = "device_state"
    pole_id: Mapped[str] = mapped_column(String, primary_key=True)
    energized: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    boot_epoch: Mapped[int] = mapped_column(Integer, default=0)
    last_event: Mapped[str | None] = mapped_column(String, nullable=True)
    device_ts: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    server_seen: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    battery_mv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rssi: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ScheduledOutage(Base):
    __tablename__ = "scheduled_outages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[str] = mapped_column(String)                         # feeder | dt
    target_id: Mapped[str] = mapped_column(String, index=True)
    start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String, default="")
    # Simulator can flag an entry as silently cancelled (feed not updated) to
    # exercise the "don't treat the feed as gospel" path.
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Stable key identifying the physical incident, used to match repeated
    # detections to the same ticket instead of spawning duplicates.
    incident_key: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="detected", index=True)
    fault_type: Mapped[str] = mapped_column(String)                   # span|dt|feeder|sensor
    feeder_id: Mapped[str | None] = mapped_column(String, nullable=True)
    dt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Localized span endpoints (upstream = last live, downstream = first dark).
    span_from_pole: Mapped[str | None] = mapped_column(String, nullable=True)
    span_to_pole: Mapped[str | None] = mapped_column(String, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    pincode: Mapped[str | None] = mapped_column(String, nullable=True)
    ward: Mapped[str | None] = mapped_column(String, nullable=True)
    poles_affected: Mapped[int] = mapped_column(Integer, default=0)
    households_affected: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_band: Mapped[str] = mapped_column(String, default="low")
    topology_source: Mapped[str] = mapped_column(String, default="none")  # recorded|inferred|none
    localization_kind: Mapped[str] = mapped_column(String, default="dt")  # span|span_range|dt|feeder
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    affected_poles: Mapped[list] = mapped_column(JSON, default=list)
    planned_match: Mapped[str | None] = mapped_column(String, nullable=True)  # scheduled-outage id if matched
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    restoration_progress: Mapped[float] = mapped_column(Float, default=0.0)
    crew_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_symptom_ts: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    events: Mapped[list["TicketEvent"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="TicketEvent.ts"
    )


class TicketEvent(Base):
    __tablename__ = "ticket_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(String, ForeignKey("tickets.id"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    kind: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text, default="")
    ticket: Mapped[Ticket] = relationship(back_populates="events")

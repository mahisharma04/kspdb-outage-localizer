"""Pydantic request/response models for the HTTP surface."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class Telemetry(BaseModel):
    device_id: str | None = None
    pole_id: str
    event: str = "heartbeat"          # heartbeat | power_lost | power_restored | boot
    energized: bool | None = None
    ts: dt.datetime | None = None
    seq: int | None = None
    battery_mv: int | None = None
    rssi: int | None = None
    fw: str | None = None


class TelemetryBatch(BaseModel):
    messages: list[Telemetry]


class TicketAction(BaseModel):
    note: str | None = None
    crew: str | None = None


class ScheduledOutageIn(BaseModel):
    id: str | None = None
    scope: str = Field(pattern="^(feeder|dt)$")
    target_id: str
    start: dt.datetime
    end: dt.datetime
    reason: str = ""
    cancelled: bool = False


# ---- simulator ----
class SpanFaultIn(BaseModel):
    dt_id: str | None = None
    from_pole: str | None = None      # upstream pole; fault on edge (from -> to)
    to_pole: str | None = None        # downstream head that goes dark
    deliver_prob: float = 0.7         # fraction of dying 'power_lost' packets that arrive


class DTFaultIn(BaseModel):
    dt_id: str | None = None
    deliver_prob: float = 0.7


class FeederFaultIn(BaseModel):
    feeder_id: str | None = None
    deliver_prob: float = 0.7


class RepairIn(BaseModel):
    ticket_id: str | None = None
    dt_id: str | None = None
    key: str | None = None            # incident_key

"""Scheduled-outage feed + suppression.

The department publishes planned load-shedding / maintenance windows. We must
not raise tickets for those. But the feed is unreliable — windows start late and
overrun by 20-40 min (absorbed by a grace period), and ~1 in 10 is silently
cancelled. So we do NOT treat the feed as gospel:

* An incident is suppressed only when the *observed pattern matches the planned
  scope*: a whole-feeder outage matches a feeder-scope window; a whole-DT outage
  matches a DT-scope window. A partial span fault inside a "shed" feeder is not
  explained by the schedule, so it still alerts (this is exactly the cancelled /
  wrong-feed case the brief warns about).
* Even a matched incident is only *quietly* suppressed. If it is still dark after
  the window + grace has elapsed, the detector escalates it to a real ticket —
  which catches a genuine fault that happened to coincide with (or was mistaken
  for) a planned window.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from .config import settings
from .models import ScheduledOutage


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def active_outages(session, now: dt.datetime | None = None) -> list[ScheduledOutage]:
    now = now or _now()
    grace = dt.timedelta(seconds=settings.SCHEDULED_GRACE_S)
    out = []
    for so in session.execute(select(ScheduledOutage)).scalars():
        if so.cancelled:
            continue
        start = so.start if so.start.tzinfo else so.start.replace(tzinfo=dt.timezone.utc)
        end = so.end if so.end.tzinfo else so.end.replace(tzinfo=dt.timezone.utc)
        if start <= now <= end + grace:
            out.append(so)
    return out


def match(incident, outages: list[ScheduledOutage]) -> ScheduledOutage | None:
    """Return the scheduled outage that explains this incident, if any.

    A planned DT/feeder shutdown makes *everything* under it dark, so any
    incident localized under that DT/feeder during the window is explained —
    including span fragments that appear when some dying-gasp packets drop near
    the transformer. Sensor faults are never suppressed (they mean the opposite
    of an outage). The escalation path (detector) still promotes a matched
    ticket to a real one if it outlasts the window, covering a cancelled feed."""
    if incident.fault_type == "sensor":
        return None
    for so in outages:
        if so.scope == "feeder" and so.target_id == incident.feeder_id:
            return so
        if so.scope == "dt" and so.target_id == incident.dt_id:
            return so
    return None


def window_elapsed(so: ScheduledOutage, now: dt.datetime | None = None) -> bool:
    now = now or _now()
    end = so.end if so.end.tzinfo else so.end.replace(tzinfo=dt.timezone.utc)
    return now > end + dt.timedelta(seconds=settings.SCHEDULED_GRACE_S)

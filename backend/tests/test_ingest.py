"""Unit tests for de-duplication and out-of-order / stale ordering in the store."""
from __future__ import annotations

import datetime as dt

from app.state import LivenessStore


def _msg(pole, event, energized, ts, seq):
    return {"pole_id": pole, "device_id": "D", "event": event,
            "energized": energized, "ts": ts.isoformat(), "seq": seq}


def test_exact_duplicate_seq_is_dropped():
    s = LivenessStore()
    t = dt.datetime(2026, 7, 29, 2, 0, tzinfo=dt.timezone.utc)
    assert s.apply(_msg("P1", "power_lost", False, t, 10)).accepted
    r = s.apply(_msg("P1", "power_lost", False, t, 10))     # duplicate
    assert r.accepted is False


def test_stale_message_after_restore_is_ignored():
    s = LivenessStore()
    t0 = dt.datetime(2026, 7, 29, 2, 0, tzinfo=dt.timezone.utc)
    s.apply(_msg("P1", "power_lost", False, t0, 5))
    # restore later
    s.apply(_msg("P1", "power_restored", True, t0 + dt.timedelta(minutes=5), 6))
    assert s.rt["P1"].energized is True
    # a very late retry of the old power_lost (older ts) must NOT flip it dark
    late = s.apply(_msg("P1", "power_lost", False, t0, 99))
    assert s.rt["P1"].energized is True
    assert late.transition is False


def test_out_of_order_newer_wins():
    s = LivenessStore()
    t0 = dt.datetime(2026, 7, 29, 2, 0, tzinfo=dt.timezone.utc)
    # newer 'restored' arrives first, then older 'power_lost' arrives late
    s.apply(_msg("P1", "power_restored", True, t0 + dt.timedelta(minutes=2), 8))
    s.apply(_msg("P1", "power_lost", False, t0, 7))
    assert s.rt["P1"].energized is True


def test_boot_resets_and_is_applied():
    s = LivenessStore()
    t0 = dt.datetime(2026, 7, 29, 2, 0, tzinfo=dt.timezone.utc)
    s.apply(_msg("P1", "power_lost", False, t0, 40))
    r = s.apply(_msg("P1", "boot", True, t0 + dt.timedelta(minutes=10), 0))
    assert r.transition is True
    assert s.rt["P1"].energized is True
    assert s.rt["P1"].boot_epoch == 1

"""Central configuration.

Everything tunable lives here so the algorithm's behaviour is documented in one
place and can be overridden by environment variables at deploy time.
"""
from __future__ import annotations

import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


class Settings:
    # --- Storage ---------------------------------------------------------
    # Default to SQLite so a reviewer with only Docker (or nothing) gets a
    # working system with zero external services. docker-compose overrides
    # this with a Postgres URL. Render can inject its own DATABASE_URL.
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", "sqlite:////tmp/kspdb.db"
    )

    # --- Synthetic network shape (seed) ----------------------------------
    SEED_FEEDERS: int = _i("SEED_FEEDERS", 6)
    SEED_DTS: int = _i("SEED_DTS", 48)
    SEED_TARGET_POLES: int = _i("SEED_TARGET_POLES", 4200)
    # Fraction of DTs whose pole ordering (seq_on_line/parent_pole_id) is
    # missing in the export — the assignment's central difficulty (~60%).
    SEED_MISSING_TOPOLOGY_FRAC: float = _f("SEED_MISSING_TOPOLOGY_FRAC", 0.60)
    SEED_HARD_DTS: int = _i("SEED_HARD_DTS", 3)  # DTs with near-parallel lines
    SEED_NO_DEVICE_FRAC: float = _f("SEED_NO_DEVICE_FRAC", 0.09)   # ~9% poles
    SEED_MISSING_PIN_FRAC: float = _f("SEED_MISSING_PIN_FRAC", 0.03)  # ~3%
    SEED_FW12_FRAC: float = _f("SEED_FW12_FRAC", 0.08)  # firmware 1.2.x (silent)
    SEED_RANDOM_SEED: int = _i("SEED_RANDOM_SEED", 7)

    # --- Detection tuning ------------------------------------------------
    # A pole is considered "silent" (possibly dark) if we have not heard from
    # it for this many seconds. Heartbeat cadence is 15 min; we allow ~2.2
    # intervals before treating silence as suspicious.
    SILENCE_TIMEOUT_S: int = _i("SILENCE_TIMEOUT_S", 2000)
    # Detection debounce: wait this long after the first symptom before
    # emitting/adjusting an incident, so a burst settles into one answer.
    DEBOUNCE_S: float = _f("DEBOUNCE_S", 4.0)
    # Grace period added to a scheduled outage's [start,end] to absorb the
    # "start late / overrun by 20-40 min" real-world behaviour.
    SCHEDULED_GRACE_S: int = _i("SCHEDULED_GRACE_S", 2700)  # 45 min
    # Device clock skew tolerance (spec: +/-90 s).
    CLOCK_SKEW_S: int = _i("CLOCK_SKEW_S", 90)
    # A single isolated dark pole whose downstream children are still live is
    # physically impossible as a line fault -> treated as a lying sensor.
    # Minimum dark poles under a boundary before we trust it as a real outage
    # when NOT corroborated by a power_lost packet.
    MIN_DARK_FOR_SILENT_OUTAGE: int = _i("MIN_DARK_FOR_SILENT_OUTAGE", 2)
    # Restoration: fraction of a ticket's device-equipped dark poles that must
    # report energized again before we auto-verify.
    RESTORE_FRACTION: float = _f("RESTORE_FRACTION", 0.6)

    # --- Ingest ----------------------------------------------------------
    # Retries can arrive up to 6h late; anything older than this we still
    # accept but treat as historical (seq decides current state anyway).
    MAX_MESSAGE_AGE_S: int = _i("MAX_MESSAGE_AGE_S", 6 * 3600)

    APP_NAME: str = "KSPDB Outage Localizer"
    SUBDIVISION: str = os.environ.get("SUBDIVISION", "SD07 — Bengaluru South")


settings = Settings()

"""Shared fixtures.

Binds a temp on-disk SQLite DB BEFORE importing the app (the engine is created
at import from DATABASE_URL), seeds a small network once, and hands each test a
fresh in-memory Detector/Simulator with the volatile tables cleared — so tests
are isolated without any module reloading.
"""
from __future__ import annotations

import os
import pathlib
import tempfile

_DB = pathlib.Path(tempfile.gettempdir()) / "kspdb_pytest.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ.setdefault("SEED_FEEDERS", "2")
os.environ.setdefault("SEED_DTS", "8")
os.environ.setdefault("SEED_HARD_DTS", "1")
os.environ.setdefault("DEBOUNCE_S", "0")

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.detector import Detector  # noqa: E402
from app.models import DeviceState, Feeder, ScheduledOutage, Ticket, TicketEvent  # noqa: E402
from app.seed import seed_network  # noqa: E402
from app.simulator import Simulator  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402


class StubPipeline:
    """Applies telemetry to the store synchronously — no async timing in tests."""
    def __init__(self, detector):
        self.detector = detector
        self.count = 0

    async def enqueue(self, msg):
        self.detector.store.apply(msg)
        self.count += 1


@pytest.fixture(scope="session", autouse=True)
def _seed_once():
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        if not s.execute(select(Feeder)).first():
            seed_network(s)


@pytest.fixture()
def system():
    with SessionLocal() as s:
        s.execute(delete(TicketEvent))
        s.execute(delete(Ticket))
        s.execute(delete(DeviceState))
        s.execute(delete(ScheduledOutage))
        s.commit()
    det = Detector()
    det.load()
    pipe = StubPipeline(det)
    sim = Simulator(det, pipe)
    sim.load()
    return det, sim

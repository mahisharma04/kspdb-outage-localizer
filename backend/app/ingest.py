"""Telemetry ingest pipeline.

The HTTP handler does almost nothing: it validates and drops the message on an
in-process asyncio.Queue, then returns 202. That keeps the endpoint O(1) so it
absorbs the 5,000-messages-in-10s burst without back-pressuring devices. A
background consumer drains the queue in batches, updates the in-memory liveness
store (de-dup + ordering live there), mirrors *state transitions* to the
DeviceState table, and pokes the detector on a short debounce so a burst
collapses into one localization pass.

For this single-node exercise the queue is in-process. ARCHITECTURE.md explains
the one-line swap to an external broker (NB-IoT -> MQTT -> Kafka) for the real
multi-subdivision deployment; nothing downstream of the queue changes.
"""
from __future__ import annotations

import asyncio
import time

from .config import settings
from .db import SessionLocal
from .models import DeviceState


class IngestPipeline:
    def __init__(self, detector, broadcaster=None, maxsize: int = 100_000) -> None:
        self.q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.detector = detector
        self.broadcaster = broadcaster
        self._last_run = 0.0
        self._dirty = False
        self.count = 0
        self._tasks: list[asyncio.Task] = []

    async def enqueue(self, msg: dict) -> None:
        await self.q.put(msg)

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._consume_loop()),
            asyncio.create_task(self._periodic_loop()),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()

    async def _consume_loop(self) -> None:
        while True:
            msg = await self.q.get()
            batch = [msg]
            # drain whatever else is queued right now
            while not self.q.empty() and len(batch) < 5000:
                batch.append(self.q.get_nowait())
            transitions = self._apply_batch(batch)
            self.count += len(batch)
            if transitions:
                self._dirty = True
            now = time.time()
            if self._dirty and (now - self._last_run) >= settings.DEBOUNCE_S:
                await self._detect()

    async def _periodic_loop(self) -> None:
        # Catches silence-timeout darkness, planned-window escalation, and any
        # dirty state that the debounce hasn't flushed yet.
        while True:
            await asyncio.sleep(max(2.0, settings.DEBOUNCE_S))
            await self._detect(force_periodic=True)

    async def _detect(self, force_periodic: bool = False) -> None:
        if not self._dirty and not force_periodic:
            return
        self._dirty = False
        self._last_run = time.time()
        events = await self.detector.run()
        if events and self.broadcaster is not None:
            for ev in events:
                await self.broadcaster.publish(ev)

    def _apply_batch(self, batch: list[dict]) -> int:
        transitions = 0
        changed: dict[str, object] = {}
        for msg in batch:
            res = self.detector.store.apply(msg)
            if res.transition and res.pole_id:
                transitions += 1
                changed[res.pole_id] = self.detector.store.rt[res.pole_id]
        if changed:
            self._persist_transitions(changed)
        return transitions

    def _persist_transitions(self, changed: dict) -> None:
        with SessionLocal() as s:
            for pid, rt in changed.items():
                row = s.get(DeviceState, pid) or DeviceState(pole_id=pid)
                row.energized = rt.energized
                row.last_seq = rt.last_seq
                row.boot_epoch = rt.boot_epoch
                row.device_ts = rt.dark_ts if rt.energized is False else rt.last_live_ts
                row.battery_mv = rt.battery_mv
                row.rssi = rt.rssi
                s.merge(row)
            s.commit()

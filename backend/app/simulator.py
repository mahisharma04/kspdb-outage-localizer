"""Fault simulator.

We are not given a data generator; building one is part of the work, because how
you simulate reveals whether you understood the physics. This simulator uses the
*ground-truth* wiring (true_parent_pole_id) — which the localizer never sees — so
an injected fault darkens exactly the poles that would really lose power, and
then produces the telemetry such a fault would actually cause:

* the ~30% of dying 'power_lost' packets that never arrive (deliver_prob),
* the firmware-1.2 devices that send nothing and just go silent,
* poles with no device that emit nothing at all,
* duplicates and out-of-order / stale messages on request,
* a dead sensor whose children stay live (must NOT become an outage),
* a scheduled outage (must NOT become a ticket).

Repairing a fault emits boot + power_restored so the detector can auto-verify
restoration from telemetry.
"""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import select

from .models import DistributionTransformer, Pole, ScheduledOutage


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Simulator:
    def __init__(self, detector, pipeline) -> None:
        self.detector = detector
        self.pipeline = pipeline
        self.rng = random.Random(1234)
        self._seq: dict[str, int] = {}
        # ground-truth adjacency (simulator-only knowledge)
        self.true_children: dict[str, list[str]] = {}
        self.pole: dict[str, dict] = {}
        self.dt_poles: dict[str, list[str]] = {}
        self.feeder_dts: dict[str, list[str]] = {}
        self.active: dict[str, list[str]] = {}   # incident_key -> darkened device poles
        self.recorded_dts: set[str] = set()
        self._loaded = False

    def load(self) -> None:
        from .db import SessionLocal
        with SessionLocal() as s:
            for p in s.execute(select(Pole)).scalars():
                self.pole[p.id] = {
                    "id": p.id, "dt_id": p.dt_id, "feeder_id": p.feeder_id,
                    "device_id": p.device_id, "fw": p.fw,
                    "true_parent": p.true_parent_pole_id,
                }
                self.dt_poles.setdefault(p.dt_id, []).append(p.id)
                if p.true_parent_pole_id:
                    self.true_children.setdefault(p.true_parent_pole_id, []).append(p.id)
            for d in s.execute(select(DistributionTransformer)).scalars():
                self.feeder_dts.setdefault(d.feeder_id, []).append(d.id)
                if d.topology_known:
                    self.recorded_dts.add(d.id)
        self._loaded = True

    # ---- helpers --------------------------------------------------------
    def _seq_next(self, pole_id: str) -> int:
        base = self._seq.get(pole_id)
        if base is None:
            rt = self.detector.store.rt.get(pole_id)
            base = (rt.last_seq or 0) if rt else 0
        base += 1
        self._seq[pole_id] = base
        return base

    def _subtree(self, root: str) -> list[str]:
        out, stack = [], [root]
        while stack:
            x = stack.pop()
            out.append(x)
            stack.extend(self.true_children.get(x, ()))
        return out

    async def _emit(self, pole_id: str, event: str, energized: bool, ts: dt.datetime | None = None):
        p = self.pole[pole_id]
        if not p["device_id"]:
            return  # no device -> no telemetry at all
        await self.pipeline.enqueue({
            "device_id": p["device_id"], "pole_id": pole_id, "event": event,
            "energized": energized, "ts": (ts or _now()).isoformat(),
            "seq": self._seq_next(pole_id), "fw": p["fw"],
            "battery_mv": self.rng.randint(3300, 3900), "rssi": self.rng.randint(-105, -70),
        })

    async def _darken(self, poles: list[str], deliver_prob: float) -> list[str]:
        """Emit the telemetry a real outage over ``poles`` would produce."""
        darkened = []
        order = list(poles)
        self.rng.shuffle(order)                       # arrive out of order
        for pid in order:
            p = self.pole[pid]
            if not p["device_id"]:
                continue
            darkened.append(pid)
            fw = p["fw"] or ""
            if fw.startswith("1.2"):
                continue                              # fw 1.2 sends nothing, goes silent
            if self.rng.random() <= deliver_prob:     # dying gasp reaches us
                await self._emit(pid, "power_lost", False)
        return darkened

    # ---- fault injection ------------------------------------------------
    async def inject_span(self, dt_id=None, from_pole=None, to_pole=None, deliver_prob=0.7) -> dict:
        if to_pole is None:
            dt_id = dt_id or self.rng.choice([d for d, ps in self.dt_poles.items() if len(ps) >= 8])
            # pick a non-root pole with a decent subtree for a clear span fault
            candidates = [
                pid for pid in self.dt_poles[dt_id]
                if self.pole[pid]["true_parent"] and len(self._subtree(pid)) >= 3
            ]
            to_pole = self.rng.choice(candidates or self.dt_poles[dt_id])
        dt_id = self.pole[to_pole]["dt_id"]
        from_pole = self.pole[to_pole]["true_parent"] or dt_id
        region = self._subtree(to_pole)
        darkened = await self._darken(region, deliver_prob)
        key = f"{dt_id}:span:{to_pole}"
        self.active[key] = darkened
        return {"type": "span", "dt_id": dt_id, "from_pole": from_pole, "to_pole": to_pole,
                "downstream_poles": len(region), "darkened_devices": len(darkened),
                "incident_key": key}

    async def inject_dt(self, dt_id=None, deliver_prob=0.7) -> dict:
        dt_id = dt_id or self.rng.choice(list(self.dt_poles))
        region = list(self.dt_poles[dt_id])
        darkened = await self._darken(region, deliver_prob)
        key = f"{dt_id}:dt"
        self.active[key] = darkened
        return {"type": "dt", "dt_id": dt_id, "poles": len(region),
                "darkened_devices": len(darkened), "incident_key": key}

    async def inject_feeder(self, feeder_id=None, deliver_prob=0.7) -> dict:
        feeder_id = feeder_id or self.rng.choice(list(self.feeder_dts))
        region = [p for d in self.feeder_dts[feeder_id] for p in self.dt_poles[d]]
        darkened = await self._darken(region, deliver_prob)
        key = f"{feeder_id}:feeder"
        self.active[key] = darkened
        return {"type": "feeder", "feeder_id": feeder_id, "dts": len(self.feeder_dts[feeder_id]),
                "poles": len(region), "darkened_devices": len(darkened), "incident_key": key}

    # ---- noise ----------------------------------------------------------
    async def inject_dead_sensor(self, pole_id=None) -> dict:
        """A single pole reports dark while its children stay live -> sensor lie."""
        if pole_id is None:
            # Prefer a pole in a recorded-topology DT with device-equipped
            # children, so the "live children" the operator sees line up with
            # the graph the localizer reasons on.
            cands = [
                p for p, kids in self.true_children.items()
                if self.pole[p]["dt_id"] in self.recorded_dts
                and any(self.pole[c]["device_id"] for c in kids)
                and self.pole[p]["device_id"]
            ]
            pole_id = self.rng.choice(cands or [p for p, k in self.true_children.items() if k])
        await self._emit(pole_id, "power_lost", False)
        # children keep heartbeating live, timestamped AFTER the false 'dark'
        later = _now() + dt.timedelta(seconds=2)
        for c in self.true_children.get(pole_id, []):
            await self._emit(c, "heartbeat", True, ts=later)
        return {"type": "dead_sensor", "pole_id": pole_id,
                "note": "children reported live after the dark -> should be flagged sensor, not outage"}

    async def inject_duplicates_and_reorder(self, pole_id=None) -> dict:
        pid = pole_id or self.rng.choice(list(self.pole))
        stale = _now() - dt.timedelta(hours=5)     # a very late retry
        await self._emit(pid, "heartbeat", True)
        await self._emit(pid, "heartbeat", True, ts=stale)   # out-of-order/stale
        # exact duplicate (same seq) — reuse last seq
        p = self.pole[pid]
        if p["device_id"]:
            await self.pipeline.enqueue({
                "device_id": p["device_id"], "pole_id": pid, "event": "heartbeat",
                "energized": True, "ts": _now().isoformat(),
                "seq": self._seq.get(pid, 1), "fw": p["fw"],
            })
        return {"type": "noise", "pole_id": pid, "note": "sent duplicate + 5h-stale message"}

    async def add_scheduled_outage(self, scope, target_id, minutes=60, reason="Load shedding",
                                   darken=True, deliver_prob=0.9) -> dict:
        from .db import SessionLocal
        if not target_id:
            target_id = (self.rng.choice(list(self.feeder_dts))
                         if scope == "feeder" else self.rng.choice(list(self.dt_poles)))
        so_id = f"SO-{int(_now().timestamp())}"
        start = _now() - dt.timedelta(minutes=1)
        end = start + dt.timedelta(minutes=minutes)
        with SessionLocal() as s:
            s.add(ScheduledOutage(id=so_id, scope=scope, target_id=target_id,
                                  start=start, end=end, reason=reason))
            s.commit()
        info = {"type": "scheduled_outage", "id": so_id, "scope": scope, "target_id": target_id}
        if darken:
            if scope == "feeder":
                r = await self.inject_feeder(target_id, deliver_prob)
            else:
                r = await self.inject_dt(target_id, deliver_prob)
            info["darkened"] = r
        return info

    # ---- repair ---------------------------------------------------------
    async def repair(self, key=None, dt_id=None) -> dict:
        keys = []
        if key and key in self.active:
            keys = [key]
        elif dt_id:
            keys = [k for k in self.active if k.startswith(f"{dt_id}:")]
        else:
            keys = list(self.active)
        restored = 0
        for k in keys:
            for pid in self.active.pop(k, []):
                await self._emit(pid, "boot", True)
                await self._emit(pid, "power_restored", True)
                restored += 1
        return {"repaired_keys": keys, "restored_devices": restored}

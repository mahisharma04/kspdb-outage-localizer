"""FastAPI application: ingest, tickets, simulator, SSE, and static console."""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select

from . import scheduled, tickets
from .bus import EventBus
from .config import settings
from .db import Base, SessionLocal, engine
from .detector import Detector
from .ingest import IngestPipeline
from .models import DeviceState, DistributionTransformer, Feeder, Pole, ScheduledOutage, Ticket, TicketEvent
from .schemas import (
    DTFaultIn, FeederFaultIn, RepairIn, ScheduledOutageIn, SpanFaultIn, Telemetry, TelemetryBatch,
)
from .seed import seed_network
from .simulator import Simulator

bus = EventBus()
detector = Detector(broadcaster=bus)
pipeline = IngestPipeline(detector, broadcaster=bus)
simulator = Simulator(detector, pipeline)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        if not s.execute(select(Feeder)).first():
            seed_network(s)
    detector.load()
    simulator.load()
    pipeline.start()
    yield
    await pipeline.stop()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ==========================================================================
# Ingest
# ==========================================================================
@app.post("/api/telemetry", status_code=202)
async def ingest_one(msg: Telemetry):
    await pipeline.enqueue(msg.model_dump(mode="json"))
    return {"accepted": True}


@app.post("/api/telemetry/batch", status_code=202)
async def ingest_batch(batch: TelemetryBatch):
    for m in batch.messages:
        await pipeline.enqueue(m.model_dump(mode="json"))
    return {"accepted": True, "count": len(batch.messages)}


# ==========================================================================
# Network (for the map / topology views)
# ==========================================================================
@app.get("/api/network/summary")
def network_summary():
    g = detector.graph
    recorded = sum(1 for d in g.dts.values() if d.topology_source == "recorded")
    return {
        "subdivision": settings.SUBDIVISION,
        "feeders": len(g.feeders), "dts": len(g.dts), "poles": len(g.poles),
        "poles_with_device": sum(1 for p in g.poles.values() if p.has_device),
        "dts_topology_recorded": recorded,
        "dts_topology_inferred": len(g.dts) - recorded,
        "ingested_messages": pipeline.count,
    }


@app.get("/api/network/poles")
def network_poles():
    """Compact pole list for the map. State overlaid from the liveness store."""
    g = detector.graph
    store = detector.store
    out = []
    for pid, p in g.poles.items():
        rt = store.rt.get(pid)
        energized = None if rt is None else rt.energized
        out.append([pid, round(p.lat, 6), round(p.lon, 6), p.dt_id, p.feeder_id,
                    1 if p.has_device else 0, energized])
    return {"columns": ["pole_id", "lat", "lon", "dt_id", "feeder_id", "has_device", "energized"],
            "poles": out}


@app.get("/api/network/dts")
def network_dts():
    g = detector.graph
    return {"dts": [
        {"dt_id": d.id, "feeder_id": d.feeder_id, "lat": d.lat, "lon": d.lon,
         "households": d.households_served, "topology_source": d.topology_source,
         "inference_quality": d.inference_quality, "poles": len(d.pole_ids)}
        for d in g.dts.values()
    ]}


# ==========================================================================
# Tickets
# ==========================================================================
@app.get("/api/tickets")
def list_tickets(scope: str = Query("active")):
    with SessionLocal() as s:
        q = select(Ticket)
        if scope == "active":
            q = q.where(Ticket.status.in_(("detected", "acknowledged", "assigned", "resolved_unverified")))
        elif scope == "planned":
            q = q.where(Ticket.status == "planned")
        elif scope == "closed":
            q = q.where(Ticket.status.in_(("verified", "closed")))
        rows = s.execute(q).scalars().all()
        data = [tickets.serialize(t) for t in rows]
    # severity ordering: most households first, then confidence
    data.sort(key=lambda t: (t["households_affected"], t["confidence"]), reverse=True)
    return {"tickets": data}


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    with SessionLocal() as s:
        t = s.get(Ticket, ticket_id)
        if not t:
            raise HTTPException(404, "ticket not found")
        return tickets.serialize(t)


async def _mutate_ticket(ticket_id: str, fn):
    with SessionLocal() as s:
        t = s.get(Ticket, ticket_id)
        if not t:
            raise HTTPException(404, "ticket not found")
        result = fn(s, t)
        s.commit()
        payload = tickets.serialize(t)
    await bus.publish({"type": "ticket_updated", "ticket": payload})
    return result if result is not None else payload


@app.post("/api/tickets/{ticket_id}/acknowledge")
async def ack_ticket(ticket_id: str, body: dict = Body(default={})):
    return await _mutate_ticket(ticket_id, lambda s, t: tickets.acknowledge(s, t, body.get("note")))


@app.post("/api/tickets/{ticket_id}/assign")
async def assign_ticket(ticket_id: str, body: dict = Body(default={})):
    return await _mutate_ticket(ticket_id, lambda s, t: tickets.assign(s, t, body.get("crew"), body.get("note")))


@app.post("/api/tickets/{ticket_id}/resolve")
async def resolve_ticket(ticket_id: str, body: dict = Body(default={})):
    return await _mutate_ticket(
        ticket_id, lambda s, t: tickets.mark_resolved(s, t, detector.store, body.get("note")))


# ==========================================================================
# Scheduled outage feed (the mocked department API)
# ==========================================================================
@app.get("/scheduled-outages")
def scheduled_feed(from_: str = Query(None, alias="from"), to: str = Query(None)):
    with SessionLocal() as s:
        rows = s.execute(select(ScheduledOutage)).scalars().all()
        return [
            {"id": r.id, "scope": r.scope, "target_id": r.target_id,
             "start": r.start.isoformat(), "end": r.end.isoformat(),
             "reason": r.reason, "cancelled": r.cancelled}
            for r in rows
        ]


@app.post("/scheduled-outages")
def add_scheduled(o: ScheduledOutageIn):
    with SessionLocal() as s:
        so_id = o.id or f"SO-{int(dt.datetime.now(dt.timezone.utc).timestamp())}"
        s.add(ScheduledOutage(id=so_id, scope=o.scope, target_id=o.target_id,
                              start=o.start, end=o.end, reason=o.reason, cancelled=o.cancelled))
        s.commit()
    return {"id": so_id}


# ==========================================================================
# Simulator
# ==========================================================================
@app.post("/api/sim/span")
async def sim_span(f: SpanFaultIn):
    return await simulator.inject_span(f.dt_id, f.from_pole, f.to_pole, f.deliver_prob)


@app.post("/api/sim/dt")
async def sim_dt(f: DTFaultIn):
    return await simulator.inject_dt(f.dt_id, f.deliver_prob)


@app.post("/api/sim/feeder")
async def sim_feeder(f: FeederFaultIn):
    return await simulator.inject_feeder(f.feeder_id, f.deliver_prob)


@app.post("/api/sim/dead-sensor")
async def sim_dead_sensor(body: dict = Body(default={})):
    return await simulator.inject_dead_sensor(body.get("pole_id"))


@app.post("/api/sim/noise")
async def sim_noise(body: dict = Body(default={})):
    return await simulator.inject_duplicates_and_reorder(body.get("pole_id"))


@app.post("/api/sim/scheduled")
async def sim_scheduled(body: dict = Body(default={})):
    return await simulator.add_scheduled_outage(
        scope=body.get("scope", "dt"), target_id=body.get("target_id"),
        minutes=body.get("minutes", 60), reason=body.get("reason", "Load shedding"),
        darken=body.get("darken", True))


@app.post("/api/sim/repair")
async def sim_repair(f: RepairIn):
    if f.ticket_id:
        with SessionLocal() as s:
            t = s.get(Ticket, f.ticket_id)
            key = t.incident_key if t else None
        return await simulator.repair(key=key, dt_id=f.dt_id)
    return await simulator.repair(key=f.key, dt_id=f.dt_id)


@app.post("/api/sim/reset")
async def sim_reset():
    """Restore everything to a clean, all-live state for a fresh demo."""
    await simulator.repair()
    with SessionLocal() as s:
        s.execute(delete(TicketEvent))
        s.execute(delete(Ticket))
        s.execute(delete(DeviceState))
        s.execute(delete(ScheduledOutage))
        s.commit()
    detector.store.prime_all_live(detector.graph)
    simulator.active.clear()
    await bus.publish({"type": "reset"})
    return {"reset": True}


# ==========================================================================
# SSE stream
# ==========================================================================
@app.get("/api/stream")
async def stream(request: Request):
    q = bus.subscribe()

    async def gen():
        try:
            yield "event: ping\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/health")
def health():
    return {"status": "ok", "poles": len(detector.graph.poles) if detector.graph else 0}


# ==========================================================================
# Static frontend (built React app) — served last so /api wins.
# ==========================================================================
_DIST = Path(os.environ.get("FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
else:
    @app.get("/")
    def _root():
        return JSONResponse({"message": "Backend up. Frontend not built; run the frontend dev server or docker compose.",
                             "docs": "/docs", "health": "/api/health"})

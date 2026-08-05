# Deployment

Written for someone who has this repo and nothing else.

## Prerequisites

- **Docker** with the Compose plugin (Docker Desktop 4.x, or Docker Engine ≥ 24
  with `docker compose`). Nothing else — no local Python, Node, or Postgres.
- For local (non-Docker) development only: Python 3.11+ and Node 20+.

## Run with Docker (recommended)

```bash
git clone <THIS_REPO_URL>
cd kspdb-outage-localizer
docker compose up --build
```

- Brings up **two** services: `db` (PostgreSQL, with a healthcheck the app waits
  on) and `app` (FastAPI + the built React console).
- The app **seeds a synthetic network on first startup** — no manual migration
  step. Tables are created automatically (`Base.metadata.create_all`).
- Open **http://localhost:8000**. You should see the console with a live pole
  map and the fault-simulator panel at the bottom-left of the map.

Stop with `Ctrl-C`; remove containers with `docker compose down`.

## Environment variables

All have safe defaults; the app runs with an empty `.env`. See
[`.env.example`](.env.example).

| Variable | What it does | Required | Default |
|----------|--------------|----------|---------|
| `DATABASE_URL` | SQLAlchemy URL. `postgres://` is auto-normalised to `postgresql://`. | No | `sqlite:////tmp/kspdb.db` (compose sets Postgres) |
| `PORT` | Port uvicorn binds. Platforms like Render inject this. | No | `8000` |
| `FRONTEND_DIST` | Path to the built console. Set by the Docker image. | No | `../frontend/dist` |
| `OPENAI_API_KEY` | Enables the LLM dispatch briefing. Without it, a deterministic template is used and everything works. | No | unset |
| `OPENAI_BASE_URL`, `AI_MODEL` | Point the briefing at any OpenAI-compatible endpoint/model. | No | OpenAI / `gpt-4o-mini` |
| `SEED_*`, `DEBOUNCE_S`, `SILENCE_TIMEOUT_S`, `SCHEDULED_GRACE_S`, `RESTORE_FRACTION` | Network shape and detection tuning. | No | see `config.py` |

**No secrets are committed.** The only optional secret (`OPENAI_API_KEY`) is
read from the environment.

## Verify it worked

1. `curl http://localhost:8000/api/health` → `{"status":"ok","poles":3145}`.
2. Open http://localhost:8000 → console loads, header shows ~3,145 poles.
3. Click **Span fault** → within a second or two, one incident appears in the
   left list with a PIN and coordinates; the map flies to it.
4. Select it → **⚡ Simulate repair** → the ticket auto-verifies and moves to
   Closed without you clicking "resolved".
5. `python scripts/benchmark.py http://localhost:8000` → throughput numbers.

## Deploy a public URL (Render, free tier)

1. Push this repo to a **public** GitHub repository.
2. Render → **New + → Blueprint** → pick the repo. It reads `render.yaml`,
   builds the `Dockerfile`, and deploys a web service with health checks.
3. It comes up on `https://<name>.onrender.com`. By default it uses SQLite on
   the container's ephemeral disk (reseeds on cold start — fine for a demo). To
   make data durable, uncomment the `databases:` block and the `DATABASE_URL`
   reference in `render.yaml` and redeploy.
4. **Cold start:** the free tier sleeps after inactivity and takes ~30–60 s to
   wake. Note this in your submission so reviewers wait rather than assume it's
   down.

(Railway / Fly.io work the same way — both build the Dockerfile directly. On
Fly, `fly launch` detects the Dockerfile; set the internal port to `8000` or use
the `PORT` env.)

## Local development (no Docker)

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --reload --port 8000    # SQLite by default

# frontend (separate terminal) — proxies /api to :8000
cd frontend
npm install
npm run dev            # http://localhost:5173
```

Run tests: `cd backend && PYTHONPATH=. pytest -q`.

## Troubleshooting (failure modes actually hit while building)

**`docker compose up` can't pull images (`403 Forbidden` from registry-1.docker.io).**
Some corporate / sandboxed networks block Docker Hub. Symptom: build fails at
`Pulling postgres:16-alpine`. Fix: run on a network that can reach Docker Hub,
`docker login`, or configure a registry mirror in Docker's `daemon.json`. (The
app itself was validated against a local PostgreSQL where Hub was unreachable.)

**Foreign-key violation on first seed with Postgres** (`insert or update on
table "dts" violates foreign key constraint`). This happened because SQLite does
not enforce FKs but Postgres does, and inserting feeders/DTs/poles in one flush
didn't guarantee parent-before-child order. **Fixed** in `seed.py` by flushing
in FK order (feeders → DTs → poles). Flagged here because it's the classic
"works on SQLite, breaks on Postgres" trap: if you change the schema, keep the
insert order.

**Map is blank / grey but markers show.** Base map tiles come from
OpenStreetMap and are fetched **by the reviewer's browser**, not the server. A
blank base map means the browser couldn't reach `tile.openstreetmap.org` (rare
corporate blocks / offline). The incident and pole overlays still render on the
canvas. No server-side key is involved.

**Live updates don't stream (console shows "reconnecting").** We use
**Server-Sent Events**, not WebSockets, specifically because SSE is plain HTTP
and survives proxies that mishandle WebSocket upgrades. If a proxy still buffers
the stream, ensure it doesn't buffer `text/event-stream` (we send
`X-Accel-Buffering: no`). The console also auto-reconnects every 2 s and the
list falls back to periodic refresh, so data is never stale for long.

**Port 8000 already in use.** `docker compose` maps `8000:8000`; change the host
side, e.g. `ports: ["8080:8000"]`, or free the port
(`lsof -ti:8000 | xargs kill`).

**ARM vs x86 (Apple Silicon).** The base images (`python:3.11-slim`,
`node:20-alpine`, `postgres:16-alpine`) are multi-arch, so `docker compose up`
works on both. If you build for a different target platform, pass
`--platform linux/amd64`.

**Free-tier memory / cold start.** Steady-state memory is small (the network
graph is a few MB). If a very small free tier OOMs during seed, lower
`SEED_TARGET_POLES` / `SEED_DTS`. First request after sleep is slow — see cold
start above.

**"geocoding unavailable".** You will not see this: PIN codes come from the
registry and, for the ~3% missing, from the nearest surveyed pole — fully
offline, no key. Any filled PIN is marked approximate in the ticket reasons.

## Reset to a clean state

- From the UI: **Reset to all-live** (bottom-left) restores every pole and
  clears tickets.
- Via API: `POST /api/sim/reset`.
- Full wipe (Docker): `docker compose down -v` removes the Postgres volume; the
  next `up` reseeds from scratch.

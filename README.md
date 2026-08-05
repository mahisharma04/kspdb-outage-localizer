# KSPDB Outage Localizer

Turn a stream of "this pole is dark" signals from LT distribution poles into a
**small number of located faults** — the exact span of wire, drive-to
coordinates, PIN code, and how many homes are down — in **seconds instead of
two hours**, and confirm restoration from telemetry rather than from a button.

> Fictional utility (Karnataka State Power Distribution Board), modelled on how
> real Bengaluru ESCOMs operate. This is a take-home submission.

---

## What it does

- **Ingests** pole telemetry over HTTP, de-duplicating and ordering messages
  from clocks that disagree, and absorbing post-outage bursts.
- **Localizes** faults by finding the live/dark boundary on the radial network
  tree — one ticket per snapped wire, not 40 alerts for 40 dark poles — and
  distinguishes span vs. DT vs. feeder faults, and real outages from dead
  sensors and scheduled load-shedding.
- Handles the assignment's central difficulty head-on: **60% of transformers
  have no recorded pole ordering.** It infers the wiring geometrically, says how
  confident it is, and degrades to a DT-area answer when it cannot honestly
  claim a span.
- **Verifies restoration from telemetry** — a crew marking a ticket fixed while
  poles are still dark is rejected; the system closes tickets on its own when
  the poles actually come back.
- Ships an **operator console** for a non-engineer at 2 a.m. and a **fault
  simulator** you can drive from that same screen.

---

## Run it in one command

```bash
git clone <THIS_REPO_URL>
cd kspdb-outage-localizer
docker compose up --build
```

Then open **http://localhost:8000**. The system seeds a synthetic network
(~3,150 poles, 48 DTs, 6 feeders) on startup, so you see a working console
immediately. Click **Span fault** in the simulator panel (bottom-left of the
map) and watch a located ticket appear.

No API keys are required. Full details and troubleshooting: **[DEPLOYMENT.md](DEPLOYMENT.md)**.

## Live demo & video

- **Live URL:** https://kspdb-outage-localizer-sd5c.onrender.com (free tier; may cold-start for ~30–60 s on first hit — please wait rather than assume it is down)
- **5-minute demo video:** _<PASTE LOOM / YOUTUBE-UNLISTED / DRIVE LINK>_

## Try it in 30 seconds (from the console)

1. **Span fault** → one ticket appears, located to a span, with a PIN and a
   dispatch briefing.
2. Select it → read the confidence reasons; click **⚡ Simulate repair** →
   watch it auto-verify and close.
3. **DT fault** / **Feeder fault** → coarser, correctly-classified incidents.
4. **Dead sensor** → *no* outage ticket (flagged for maintenance instead).
5. **Sched. outage** → suppressed as *planned*, not alarmed.
6. **Dup / late msg** → ignored; nothing breaks.
7. **Reset to all-live** → clean slate.

## The documents

| File | What's in it |
|------|--------------|
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | Data flow diagram, ingestion, storage & topology model, **the localization algorithm** (incl. the 60%-missing-topology answer), noise handling, API surface, UI reasoning, and the AI feature. |
| **[DEPLOYMENT.md](DEPLOYMENT.md)** | Prereqs, exact commands, every env var, how to verify, a real troubleshooting section, and how to reset. |
| **[DECISIONS.md](DECISIONS.md)** | Decision log (newest first), assumptions, what I'd do with two more weeks, and what is currently fragile. |
| **[AI-WORKFLOW.md](AI-WORKFLOW.md)** | Which AI tools did what, what I kept vs. threw away, and concrete cases where the AI was wrong. |

## Repository layout

```
backend/          FastAPI service (ingest, localization, tickets, simulator)
  app/            application modules (see ARCHITECTURE.md for each file)
  tests/          20 tests; the localization logic is the focus
frontend/         React + Vite + react-leaflet operator console
scripts/          benchmark.py (measures the performance targets)
Dockerfile        multi-stage: build console -> serve from FastAPI
docker-compose.yml  app + Postgres, one command
render.yaml       Render deploy blueprint
```

## Stack

Python 3.11 · FastAPI · SQLAlchemy 2 · SQLite (default) / PostgreSQL (compose) ·
React 18 · Vite · Leaflet + OpenStreetMap · Server-Sent Events.

## Tests & performance (measured, not claimed)

```bash
cd backend && pip install -r requirements.txt && PYTHONPATH=. pytest -q   # 20 passed
python scripts/benchmark.py http://localhost:8000                          # throughput
```

| Target | Result on a laptop |
|--------|--------------------|
| Fault → localized ticket (< 120 s p95) | **~0.2 s** |
| Restoration → auto-verified (< 120 s) | **~2 s** |
| Ingest sustained (≥ 500 msg/s) | **~38,000 msg/s** |
| Burst 5,000 msgs / 10 s, no loss | **processed in ~0.13 s, 0 lost** |

See ARCHITECTURE.md for what these numbers do and don't prove.

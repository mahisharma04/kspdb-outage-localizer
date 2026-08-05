# Decisions

Newest first. Each entry: what I chose, what I rejected, and why. Assumptions
made where the brief was ambiguous are treated (per the brief) as correct.

---

### D-14 · Show raw pole liveness on the map, not the inferred outage region
**Chose:** the map colours poles by what telemetry literally reports (green =
last heartbeat energized, red = reported dark). **Rejected:** colouring the
whole algorithm-inferred dark region red. **Why:** the raw view is honest about
what we actually know vs. infer. **Trade-off / likely wrong:** a pole whose
dying gasp was lost shows green even though the incident counts it as dark, which
can look inconsistent. This is the decision I most expect a reviewer to push on;
a good answer is a toggle. Not done for time.

### D-13 · Inference confidence via perturbation stability, not edge heuristics
**Chose:** estimate per-DT topology-inference quality by rebuilding the MST 6×
under ±4 m GPS jitter and measuring parent stability. **Rejected:** nearest-
neighbour ratio, and edge-length-outlier heuristics. **Why:** the first two
didn't correlate with real errors (the NN ratio flagged ~90% of poles on clean
lines because up/down-stream neighbours are equidistant; edge-length flagged
almost nothing). Perturbation stability uses the one error source whose
magnitude we actually know and, validated against ground truth, catches ~85% of
true parent-inference errors. See AI-WORKFLOW.md D-13 for how this failure was
found.

### D-12 · Suppress *any* incident under a planned DT/feeder, then escalate
**Chose:** a DT/feeder scheduled window suppresses any incident localized under
it (not only exact-scope matches). **Rejected:** matching only `dt`-type
incidents to `dt`-scope windows. **Why:** partial gasp loss fragments a whole-DT
outage into span tickets; scope-exact matching let those leak past a planned
outage and alarm. Broad match + time-based escalation (still dark after
window+grace ⇒ real ticket) is both quieter and safer, and still catches the
1-in-10 cancelled window.

### D-11 · Measure restoration only over poles confirmed dark
**Chose:** `restoration_progress` = fraction of poles that *actually reported
dark* (`was_dark`) now energized. **Rejected:** fraction of all affected poles
now energized. **Why:** the ~30% of poles whose dying gasp was lost stay
"energized" in the store, so the naive version showed ~33% restored on a
brand-new fault. Confirmed-dark-only reads 0% fresh and 100% on real
restoration.

### D-10 · Sensor-lie faults go to a maintenance status, off the alarm board
**Chose:** a dark-pole-with-live-children incident becomes a `sensor_flagged`
ticket, excluded from the active-outage list. **Rejected:** raising it as a
normal (low-confidence) outage. **Why:** it is provably *not* an outage; putting
it on the alarm board is exactly the crying-wolf the brief warns against.

### D-9 · AI only writes the dispatch briefing; localization stays deterministic
**Chose:** LLM for one natural-language sentence per new ticket, with a
deterministic template fallback. **Rejected:** any LLM role in localization.
**Why:** a graph traversal is instant, free, explainable, and testable; an LLM
is none of those and the brief explicitly warns against it. Fluency at the
human interface is the one place the model adds value it can't subtract from.

### D-8 · Server-Sent Events, not WebSockets
**Chose:** SSE for live updates. **Rejected:** WebSockets. **Why:** the console
only needs server→client push; SSE is plain HTTP, survives free-tier proxies
that mishandle WS upgrades (a failure the brief calls out), and auto-reconnects.
Polling remains as a fallback.

### D-7 · Real Leaflet/OSM map (assumption: geographic view expected)
**Chose:** a geographic map with free OSM tiles (no reviewer key). **Rejected:**
a schematic tree view. **Why:** the output is "coordinates you'd put in a
vehicle's nav", so a map matches the job. Tiles load client-side, so no server
key is needed. A schematic DT view would aid topology clarity — a future toggle.

### D-6 · In-memory liveness + debounced detector; DB only for transitions/tickets
**Chose:** keep hot-path pole state in memory; persist only transitions and
tickets. **Rejected:** write every telemetry message to the DB. **Why:**
heartbeats dominate volume and carry no state change; the design keeps writes
proportional to real events and hits ~38k msg/s. Cost: liveness is lost on
restart except what `device_state` + open tickets recover.

### D-5 · Localize to a span where topology is known/high-confidence, DT-area otherwise
**Chose:** the three-regime answer (recorded span / inferred span / DT-area) with
the regime shown in the UI. **Rejected:** (a) assuming complete wiring, (b)
refusing any answer without it. **Why:** it's the central design question. We
ship a useful answer *today* for the 60% (a DT-area or a flagged inferred span)
and are explicit about which kind the operator is looking at.

### D-4 · Assumption — "one fault" = one live/dark boundary
**Chose:** each frontier edge is one incident; two boundaries on a line are two
tickets even if one crew fixes both. **Why:** the algorithm's job is *location*;
merging by crew logistics is dispatch's job (out of scope). Documented because
the brief says there's no single right answer here.

### D-3 · Synthetic network: keep hidden ground-truth wiring
**Chose:** generate the true tree for every DT, then withhold ordering from 60%
of DTs in the "export". **Why:** lets the simulator darken exactly the right
poles *and* lets me measure inference accuracy honestly, instead of hand-waving
the hard case.

### D-2 · SQLite by default, Postgres under compose
**Chose:** `DATABASE_URL` drives both; SQLite default so the app runs with zero
services (and free tiers with no DB addon). **Rejected:** Postgres-only. **Why:**
reproducibility. Cost: two code paths — mitigated by testing both (found the FK
bug, D-1-adjacent).

### D-1 · FastAPI + React + SQLAlchemy
**Chose:** the stack I'm fastest in and can explain line-by-line. No hidden
favourite per the brief. Libraries used: FastAPI, SQLAlchemy, Pydantic,
Leaflet/react-leaflet, Vite. Graph/MST is hand-rolled (small, and I wanted to
own the one algorithm that's graded).

---

## With two more weeks

1. **Topology learning from history** — poles that go dark together are
   adjacent; accumulate co-outage statistics to correct the geometric inference
   and raise confidence on the 60% over time.
2. **Map toggle** for inferred-outage-region vs. raw-liveness (D-14), and a
   schematic DT tree view (D-7).
3. **Silence-only outage detection** on a tighter path (adaptive per-pole
   heartbeat model) so all-fw-1.2 branches aren't only caught at ~33 min.
4. **Multi-node runtime** — external broker + per-feeder sharded workers +
   Redis liveness (see ARCHITECTURE §9), with a load test at 30× volume.
5. **Property-based tests** for the localizer over randomly generated
   topologies and fault sets, not just the hand-built cases.

## What is currently fragile / known-wrong

- **Confidently-wrong inference (~15% of the 5% error).** Some inferred spans are
  wrong *and* not flagged. This is the irreducible risk of the 60% case with
  today's approach; topology learning (above) is the fix.
- **Restart recovery is partial.** Liveness rebuilds from `device_state` + open
  tickets; a pole that went dark with no persisted transition and no ticket would
  come back as "live" after a restart.
- **Single-process runtime.** In-memory queue + store cap it at one node (by
  design, isolated behind the queue).
- **Raw-liveness map inconsistency** with lost gasps (D-14).
- **Simulator seq counters** are in-memory; restarting the app mid-scenario can
  desync injected `seq` from the store (only affects the simulator, not real
  ingest).

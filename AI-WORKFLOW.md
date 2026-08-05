# AI Workflow

How this was actually built. The point of this document (and the follow-up
call) is not *whether* AI was used it was, heavily. but whether I can tell
good AI output from bad and understand what shipped. The most useful evidence of
that is below: the places the AI was **wrong**, and how I caught it.

## Tools and division of labour

- **Claude (agentic coding)** — drafted most of the code: the FastAPI scaffolding,
  SQLAlchemy models, the React/Leaflet console, the Dockerfile/compose, and first
  drafts of the algorithm modules. Roughly **80–85% of the lines are
  AI-generated**; that number is not the interesting part.
- **What I directed and owned** — the *design*: that the network is a tree and
  the fault is an edge; the three-regime answer for the 60% missing topology; the
  decision to keep localization deterministic and put the LLM only on the
  briefing; the confidence model; the "verify restoration from telemetry" rule.
  These are the load-bearing choices and they came from reading the brief, not
  from the model.
- **What I wrote/steered by hand** — the localization boundary logic and the
  downstream-darkness propagation (the AI's first versions were subtly wrong, see
  below), the inference-quality metric (three attempts), and every test — I
  wrote the tests to pin behaviour I wanted, then made the code satisfy them.

I let the AI write freely where correctness is cheap to verify (CRUD endpoints,
serialization, CSS, the simulator plumbing) and slowed down to review
line-by-line where correctness is the product (localization, ingest ordering,
restoration).

## Three times the AI was confidently wrong

**1. The topology-confidence metric that looked right and wasn't.**
Asked for a way to score how trustworthy the geometric (MST) topology inference
is, the AI proposed a clean-looking heuristic: flag a pole as ambiguous if its
second-nearest neighbour is within 1.25× of its nearest. It read as reasonable.
I validated it against the simulator's hidden ground-truth wiring and it was
nonsense: it flagged ~90% of poles as ambiguous while the MST was actually
recovering **94.6%** of true parents — because on a straight line the upstream
and downstream neighbours *are* equidistant, which says nothing about ordering.
A second AI suggestion (edge-length outliers) went the other way — flagged
almost nothing (0% recall against real errors). I replaced both with a
**perturbation-stability** measure (rebuild the MST under ±4 m GPS jitter, see
which parents flip), which validated at ~85% recall of true errors. Lesson: a
plausible metric is worthless until measured against ground truth; building the
simulator with hidden truth is what made that measurement possible.

**2. "Dark pole with a live child ⇒ sensor lie" — correct rule, broken naively.**
The AI implemented the sensor-lie check literally: if a dark pole has any live
descendant, call it a sensor fault. That's right in steady state but **wrong
during a real fault**: downstream poles keep their last (pre-fault) heartbeat
for up to 15 minutes, so within the detection window they still read "live", and
the naive rule would dismiss a genuine outage as a sensor glitch. The fix was to
make darkness **propagate downstream** through the tree and only treat a
descendant as truly live if it reported live *after* the upstream pole went dark
(timestamp-checked). I caught this by reasoning through the 15-minute heartbeat
cadence against the 120-second target — the numbers didn't allow the naive rule
to be safe.

**3. Works on SQLite, breaks on Postgres.**
The seed did `add_all(feeders); add_all(dts); add_all(poles); commit()` in one
flush. Every test passed on SQLite. When I ran the same code against a real
PostgreSQL (what docker-compose uses), it failed immediately with a foreign-key
violation — SQLite doesn't enforce FKs, Postgres does, and the single flush
didn't guarantee parents were inserted before children. Fixed by flushing in FK
order. I only found it because I deliberately ran the app against Postgres, not
just the SQLite path the tests use.

Two smaller ones, caught by testing over HTTP rather than trusting the code:
restoration showed **33% on a brand-new fault** (it counted poles whose dying
gasp was merely lost as "still live" ⇒ measure only confirmed-dark poles); and
scheduled-outage suppression **leaked span fragments** when gasps dropped near
the transformer (scope-exact matching ⇒ suppress anything under a planned
DT/feeder, then escalate on overrun).

## How I verified, not just vibes

- **Tests as the spec.** 20 tests, weighted at the localization logic exactly as
  the brief asks: a known fault in a known topology must produce the expected
  span, grouping, simultaneous-fault, DT/feeder/sensor, and missing-device-range
  behaviour. I wrote these to describe what I wanted before trusting the code.
- **Ground-truth validation** of topology inference (95% parent recovery; 85%
  error-flag recall) — numbers, not adjectives.
- **Ran the real thing**: booted the server, drove every fault type over HTTP,
  measured latency (~0.2 s) and throughput (~38k msg/s), and loaded the console
  in a headless browser to confirm it actually renders and the boundary shows.
- **Ran against Postgres**, not only SQLite — which is the only reason bug #3 was
  found before a reviewer would hit it.

## Best prompt / session excerpts

The most valuable prompting wasn't asking for code, it was asking for
*adversarial validation*:

- "The MST recovers 94.6% of true parents but this confidence metric reports 0.11
  mean quality. Something is wrong with the metric — reason about what a straight
  line of equally-spaced poles does to a nearest-neighbour ratio, then propose a
  measure that correlates with actual parent-recovery error."
- "Walk through a span fault where 30% of `power_lost` packets are dropped and the
  downstream poles last heartbeated 6 minutes ago. What does the sensor-lie rule
  do, and is that correct within a 120-second window?"
- "Run this against a real Postgres instead of SQLite and tell me every place the
  behaviour differs."

Those turned the AI from a code generator into a reviewer of its own output,
which is where it earned its keep on this project.

#!/usr/bin/env python3
"""Measure the performance targets from 02-data-and-systems.md against a running
instance. Usage: python scripts/benchmark.py [BASE_URL]"""
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011"
c = httpx.Client(base_url=BASE, timeout=60)


def processed():
    return c.get("/api/network/summary").json()["ingested_messages"]


def burst(n=5000, chunk=1000):
    """Fire n heartbeats as fast as possible; measure accept + drain time."""
    poles = [p[0] for p in c.get("/api/network/poles").json()["poles"] if p[5] == 1][:n]
    while len(poles) < n:
        poles += poles
    poles = poles[:n]
    start_count = processed()
    msgs = [{"pole_id": p, "event": "heartbeat", "energized": True, "seq": 900000 + i}
            for i, p in enumerate(poles)]
    t0 = time.time()
    for i in range(0, n, chunk):
        c.post("/api/telemetry/batch", json={"messages": msgs[i:i + chunk]})
    accept_dt = time.time() - t0
    # wait for the queue to drain
    while processed() - start_count < n and time.time() - t0 < 30:
        time.sleep(0.05)
    drain_dt = time.time() - t0
    print(f"burst n={n}: accepted in {accept_dt:.2f}s ({n/accept_dt:,.0f} msg/s accept), "
          f"fully processed in {drain_dt:.2f}s ({n/drain_dt:,.0f} msg/s end-to-end), "
          f"loss={n-(processed()-start_count)}")


if __name__ == "__main__":
    print("network:", c.get("/api/network/summary").json())
    burst(5000)
    burst(5000)

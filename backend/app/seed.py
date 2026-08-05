"""Synthetic network generator.

Produces a registry shaped like the real one described in 02-data-and-systems.md:

* radial LT lines with 1-5 branches, up to ~1.4 km long, 9-240 poles/DT
* ~9% of poles with no device
* ~8% of devices on firmware 1.2.x (go silent on power loss, never send power_lost)
* ~3% of poles with no pincode
* ~60% of DTs with NO recorded pole ordering (seq_on_line / parent_pole_id blank)

Crucially we always build the *true* physical tree (true_parent_pole_id /
true_seq) so the simulator can darken the correct downstream poles. We then
withhold that ordering from the exported columns for 60% of DTs. The localizer
is only ever allowed to read the exported columns — it must infer the rest.
"""
from __future__ import annotations

import math
import random

from sqlalchemy import select

from .config import settings
from .db import Base, engine
from .models import DistributionTransformer, Feeder, Pole

# Bengaluru South-ish bounding box.
LAT0, LON0 = 12.905, 77.560
LAT1, LON1 = 12.985, 77.640

# Metres-per-degree at this latitude.
M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(12.95))

PIN_POOL = ["560076", "560078", "560068", "560102", "560100", "560035"]


def _step_deg(dist_m: float, bearing_rad: float) -> tuple[float, float]:
    dlat = (dist_m * math.cos(bearing_rad)) / M_PER_DEG_LAT
    dlon = (dist_m * math.sin(bearing_rad)) / M_PER_DEG_LON
    return dlat, dlon


def _gen_dt_poles(rng: random.Random, dt_lat: float, dt_lon: float, n_target: int,
                  hard: bool = False):
    """Return list of pole nodes (dicts) forming a radial tree rooted at the DT.

    Each node: idx, lat, lon, parent_idx (None => fed directly from DT).
    Poles are created breadth-first so a parent always precedes its children.
    """
    nodes: list[dict] = []
    span = rng.uniform(28.0, 42.0)  # inter-pole span in metres

    # ---- main run --------------------------------------------------------
    main_bearing = rng.uniform(0, 2 * math.pi)
    n_main = min(n_target, rng.randint(20, 45))     # up to ~1.4 km at ~33 m spacing
    lat, lon = dt_lat, dt_lon
    prev = None
    main_idxs = []
    for _ in range(n_main):
        b = main_bearing + rng.uniform(-0.10, 0.10)  # gentle wander
        dlat, dlon = _step_deg(span + rng.uniform(-4, 4), b)
        lat, lon = lat + dlat, lon + dlon
        idx = len(nodes)
        nodes.append({"idx": idx, "lat": lat, "lon": lon, "parent": prev})
        main_idxs.append(idx)
        prev = idx

    # ---- hard case: a near-parallel return line ~6 m away ----------------
    # Models two LT lines running down opposite sides of the same street. GPS
    # (±4 m) can't reliably say which line a pole is on, so geometric inference
    # cross-links them and perturbation-stability (topology.py) correctly drops
    # the confidence -> the system falls back to a DT-area answer. This is the
    # honest "we can't localize to a span here" regime made visible.
    if hard and len(main_idxs) >= 4:
        offset = 6.0
        perp = main_bearing + math.pi / 2
        odlat, odlon = _step_deg(offset, perp)
        prev = None
        plat, plon = dt_lat + odlat, dt_lon + odlon
        for _ in range(min(n_target - len(nodes), len(main_idxs))):
            b = main_bearing + rng.uniform(-0.10, 0.10)
            dlat, dlon = _step_deg(span + rng.uniform(-4, 4), b)
            plat, plon = plat + dlat, plon + dlon
            idx = len(nodes)
            nodes.append({"idx": idx, "lat": plat, "lon": plon, "parent": prev})
            prev = idx
        return nodes

    # ---- branches / spurs ------------------------------------------------
    # Distribute the remaining poles across up to 5 branches so we hit the
    # per-DT target while keeping the "1-5 branches" shape.
    remaining = n_target - len(nodes)
    if remaining > 0 and len(main_idxs) >= 3:
        n_branches = min(5, max(1, remaining // 20 + 1))
        per = math.ceil(remaining / n_branches)
        for _ in range(n_branches):
            if remaining <= 0:
                break
            root_idx = rng.choice(main_idxs[1:-1])
            branch_bearing = main_bearing + rng.choice([-1, 1]) * rng.uniform(0.9, 1.7)
            blen = min(remaining, per, rng.randint(4, 40))
            plat, plon = nodes[root_idx]["lat"], nodes[root_idx]["lon"]
            prev = root_idx
            for _ in range(blen):
                b = branch_bearing + rng.uniform(-0.12, 0.12)
                dlat, dlon = _step_deg(span + rng.uniform(-4, 4), b)
                plat, plon = plat + dlat, plon + dlon
                idx = len(nodes)
                nodes.append({"idx": idx, "lat": plat, "lon": plon, "parent": prev})
                prev = idx
                remaining -= 1
    return nodes


def seed_network(session, *, reset: bool = True) -> dict:
    """(Re)generate the whole synthetic network. Returns summary counts."""
    Base.metadata.create_all(engine)
    if reset:
        # Order matters for FK-less SQLite too; just clear the registry tables.
        for tbl in ("poles", "dts", "feeders"):
            session.execute(__import__("sqlalchemy").text(f"DELETE FROM {tbl}"))
        session.commit()

    existing = session.execute(select(Feeder)).first()
    if existing and not reset:
        return {"skipped": True}

    rng = random.Random(settings.SEED_RANDOM_SEED)
    n_feeders = settings.SEED_FEEDERS
    n_dts = settings.SEED_DTS

    feeders, dts, poles = [], [], []
    pole_counter = 0

    # Which DTs have recorded topology.
    dt_indices = list(range(n_dts))
    rng.shuffle(dt_indices)
    n_known = round(n_dts * (1 - settings.SEED_MISSING_TOPOLOGY_FRAC))
    known_dts = set(dt_indices[:n_known])
    # A few missing-topology DTs are deliberately geometrically hard (parallel
    # lines) so the DT-area fallback regime is visible in the demo.
    hard_dts = set(dt_indices[n_known:n_known + settings.SEED_HARD_DTS])

    subs = [f"SS-{i+1:02d}" for i in range(4)]  # 4 substations in the subdivision

    for f in range(n_feeders):
        fid = f"F-07-{f+1:02d}"
        feeders.append(Feeder(id=fid, substation_id=subs[f % len(subs)]))

    # Distribute DTs across feeders.
    dt_id_list = []
    for d in range(n_dts):
        fid = feeders[d % n_feeders].id
        did = f"D-{d+100:04d}"
        dt_id_list.append(did)
        dlat = rng.uniform(LAT0, LAT1)
        dlon = rng.uniform(LON0, LON1)
        # Pole count per DT: right-skewed, median ~70, clamped 9..240.
        n_poles = int(min(240, max(9, round(rng.lognormvariate(math.log(72), 0.55)))))
        households = rng.randint(40, 400)
        capacity = rng.choice([63, 100, 160, 250, 315, 400])
        topo_known = d in known_dts
        dts.append(DistributionTransformer(
            id=did, feeder_id=fid, lat=dlat, lon=dlon,
            capacity_kva=capacity, households_served=households,
            topology_known=topo_known,
        ))

        nodes = _gen_dt_poles(rng, dlat, dlon, n_poles, hard=(d in hard_dts))
        # Assign real pole ids in creation order; build id map.
        idmap = {}
        for node in nodes:
            pole_counter += 1
            pid = f"P-{pole_counter:06d}"
            idmap[node["idx"]] = pid
        base_pin = PIN_POOL[d % len(PIN_POOL)]
        ward = f"W-{(d % 30) + 60:03d}"
        for node in nodes:
            pid = idmap[node["idx"]]
            true_parent = idmap[node["parent"]] if node["parent"] is not None else None
            true_seq = node["idx"] + 1  # BFS creation order => parent < child
            has_device = rng.random() > settings.SEED_NO_DEVICE_FRAC
            fw = None
            device_id = None
            if has_device:
                fw = "1.2.7" if rng.random() < settings.SEED_FW12_FRAC else "1.4.2"
                device_id = f"KSPDB-SD07-{did}-{node['idx']:04d}"
            pincode = None if rng.random() < settings.SEED_MISSING_PIN_FRAC else base_pin
            poles.append(Pole(
                id=pid, lat=node["lat"], lon=node["lon"],
                feeder_id=fid, dt_id=did,
                seq_on_line=(true_seq if topo_known else None),
                parent_pole_id=(true_parent if topo_known else None),
                true_seq=true_seq, true_parent_pole_id=true_parent,
                pole_type=rng.choice(["LT-9m-PCC", "LT-8m-Steel", "LT-9m-PCC"]),
                ward=ward, pincode=pincode, device_id=device_id, fw=fw,
            ))

    # Insert in FK dependency order with a flush between each level. SQLite does
    # not enforce FKs so a single commit works there, but Postgres does — flush
    # feeders before DTs, and DTs before poles, or the FK checks fail.
    session.add_all(feeders)
    session.flush()
    session.add_all(dts)
    session.flush()
    session.add_all(poles)
    session.commit()
    return {
        "feeders": len(feeders),
        "dts": len(dts),
        "poles": len(poles),
        "dts_topology_known": len(known_dts),
        "dts_topology_missing": n_dts - len(known_dts),
        "poles_no_device": sum(1 for p in poles if not p.device_id),
    }

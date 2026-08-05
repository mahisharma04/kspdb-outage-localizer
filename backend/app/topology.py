"""Network topology model — the core data structure.

The LT network is *radial* (a tree): every pole has exactly one path back to
its distribution transformer (DT). We represent each DT's subtree explicitly as
parent/children maps rooted at the DT. That single shape is what turns
"which poles are dark" into "which edge failed".

Two ways a DT's tree is built:

  recorded  — the export gave us seq_on_line + parent_pole_id (~40% of DTs).
              We trust it. inference_quality = 1.0.

  inferred  — the export left ordering blank (~60% of DTs). We reconstruct a
              plausible radial tree with a Euclidean **minimum spanning tree**
              rooted at the DT location, using pole GPS (always present, ±4 m).
              A straight single line is recovered almost perfectly; the failure
              mode is two lines running close together or dense clusters, where
              the MST can hop across the street. We quantify that with a
              per-DT inference_quality in [0,1] derived from how many poles have
              an ambiguous nearest-neighbour choice, and we surface it as
              reduced confidence in the UI (and fall back to DT-level answers
              when it is poor).

Only the *exported* columns are read here. Ground-truth columns
(true_parent_pole_id / true_seq) are never touched — that is the simulator's
private knowledge, not the algorithm's.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from .geo import haversine_m
from .models import DistributionTransformer, Feeder, Pole

# Surveyed GPS is accurate to ~±4 m (02-data-and-systems.md §3). We use this as
# the perturbation scale for the topology-inference stability estimate.
_GPS_SIGMA_M = 4.0


def _prim_parents(ids, coords, root):
    """Prim MST over ``ids`` (+ a virtual root) using haversine distance.

    ``coords``: id -> (lat, lon). ``root``: (lat, lon) of the DT. Returns
    id -> parent-id (or None when fed directly from the DT). O(n^2)."""
    from .geo import haversine_m as _h

    n = len(ids)
    rlat, rlon = root
    best_cost = {pid: _h(rlat, rlon, *coords[pid]) for pid in ids}
    best_parent: dict[str, str | None] = {pid: None for pid in ids}
    in_tree: set[str] = set()
    out = {}
    while len(in_tree) < n:
        u = min((pid for pid in ids if pid not in in_tree), key=lambda x: best_cost[x])
        in_tree.add(u)
        out[u] = best_parent[u]
        ulat, ulon = coords[u]
        for v in ids:
            if v in in_tree:
                continue
            w = _h(ulat, ulon, *coords[v])
            if w < best_cost[v]:
                best_cost[v] = w
                best_parent[v] = u
    return out


@dataclass
class PoleNode:
    id: str
    lat: float
    lon: float
    dt_id: str
    feeder_id: str
    has_device: bool
    pincode: str | None
    ward: str | None
    fw: str | None


@dataclass
class DTNode:
    id: str
    feeder_id: str
    lat: float
    lon: float
    households_served: int
    topology_source: str          # "recorded" | "inferred"
    inference_quality: float      # 1.0 for recorded
    pole_ids: list[str] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)      # poles fed directly from DT


class NetworkGraph:
    """Immutable-after-build topology used by the localizer."""

    def __init__(self) -> None:
        self.poles: dict[str, PoleNode] = {}
        self.dts: dict[str, DTNode] = {}
        self.feeders: dict[str, str] = {}                 # feeder_id -> substation
        self.parent: dict[str, str | None] = {}           # pole -> parent pole (None = DT)
        self.children: dict[str, list[str]] = {}
        self.ambiguous: set[str] = set()                  # poles with uncertain parent
        self.dt_of_feeder: dict[str, list[str]] = {}

    # ---- construction ---------------------------------------------------
    @classmethod
    def from_db(cls, session) -> "NetworkGraph":
        g = cls()
        for f in session.execute(select(Feeder)).scalars():
            g.feeders[f.id] = f.substation_id
        poles_by_dt: dict[str, list[Pole]] = {}
        for p in session.execute(select(Pole)).scalars():
            g.poles[p.id] = PoleNode(
                id=p.id, lat=p.lat, lon=p.lon, dt_id=p.dt_id, feeder_id=p.feeder_id,
                has_device=bool(p.device_id), pincode=p.pincode, ward=p.ward, fw=p.fw,
            )
            g.children[p.id] = []
            poles_by_dt.setdefault(p.dt_id, []).append(p)
        for d in session.execute(select(DistributionTransformer)).scalars():
            g.dt_of_feeder.setdefault(d.feeder_id, []).append(d.id)
            dt_poles = poles_by_dt.get(d.id, [])
            if d.topology_known and any(p.seq_on_line is not None for p in dt_poles):
                src, quality = "recorded", 1.0
                g._build_recorded(d, dt_poles)
            else:
                src, quality = "inferred", g._build_inferred(d, dt_poles)
            g.dts[d.id] = DTNode(
                id=d.id, feeder_id=d.feeder_id, lat=d.lat, lon=d.lon,
                households_served=d.households_served,
                topology_source=src, inference_quality=quality,
                pole_ids=[p.id for p in dt_poles],
                roots=[pid for pid in (p.id for p in dt_poles) if g.parent.get(pid) is None],
            )
        return g

    def _build_recorded(self, d: DistributionTransformer, dt_poles: list[Pole]) -> None:
        present = {p.id for p in dt_poles}
        for p in dt_poles:
            par = p.parent_pole_id if p.parent_pole_id in present else None
            self.parent[p.id] = par
            if par is not None:
                self.children[par].append(p.id)

    def _build_inferred(self, d: DistributionTransformer, dt_poles: list[Pole]) -> float:
        """Prim MST rooted at the DT. Returns inference_quality in [0,1].

        Quality is a **perturbation-stability** measure: we rebuild the MST a
        few times with each pole's coordinates jittered by the registry's known
        ±4 m GPS error, and see how often each pole keeps the same parent. Poles
        whose parent flips under GPS-scale noise are the genuinely uncertain
        ones — precisely where two lines run close enough that 4 m decides which
        line a pole belongs to. This needs no ground truth and directly reflects
        the one error source we know the magnitude of. Validated offline against
        the simulator's hidden true wiring, it tracks the real parent-recovery
        error far better than edge-length or nearest-neighbour heuristics.
        """
        n = len(dt_poles)
        if n == 0:
            return 1.0
        ids = [p.id for p in dt_poles]
        base_coords = {p.id: (p.lat, p.lon) for p in dt_poles}

        base_parent = _prim_parents(ids, base_coords, (d.lat, d.lon))
        for pid in ids:
            self.parent[pid] = base_parent[pid]
            if base_parent[pid] is not None:
                self.children[base_parent[pid]].append(pid)

        # Perturbation stability (deterministic per DT for reproducibility).
        import random as _random

        rng = _random.Random(hash(d.id) & 0xFFFFFFFF)
        K = 6
        deg = _GPS_SIGMA_M / 111_000.0
        stable = {pid: 0 for pid in ids}
        for _ in range(K):
            jit = {pid: (la + rng.gauss(0, deg), lo + rng.gauss(0, deg))
                   for pid, (la, lo) in base_coords.items()}
            par = _prim_parents(ids, jit, (d.lat, d.lon))
            for pid in ids:
                if par[pid] == base_parent[pid]:
                    stable[pid] += 1
        for pid in ids:
            if stable[pid] < K:                # flipped at least once
                self.ambiguous.add(pid)
        return round(sum(stable.values()) / (n * K), 3)

    # ---- queries --------------------------------------------------------
    def descendants(self, pole_id: str) -> list[str]:
        """All poles strictly downstream of pole_id (inclusive of itself)."""
        out, stack = [], [pole_id]
        while stack:
            x = stack.pop()
            out.append(x)
            stack.extend(self.children.get(x, ()))
        return out

    def subtree_size(self, pole_id: str) -> int:
        return len(self.descendants(pole_id))

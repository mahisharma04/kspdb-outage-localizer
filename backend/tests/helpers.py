"""Test helpers: build small, known NetworkGraphs by hand (no DB needed)."""
from __future__ import annotations

from app.topology import DTNode, NetworkGraph, PoleNode


class GraphBuilder:
    def __init__(self):
        self.g = NetworkGraph()

    def feeder(self, fid: str, substation: str = "SS-01"):
        self.g.feeders[fid] = substation
        self.g.dt_of_feeder.setdefault(fid, [])
        return self

    def dt(self, dt_id, feeder_id, lat=12.97, lon=77.59, households=100,
           source="recorded", quality=1.0):
        self.g.dts[dt_id] = DTNode(
            id=dt_id, feeder_id=feeder_id, lat=lat, lon=lon,
            households_served=households, topology_source=source,
            inference_quality=quality,
        )
        self.g.dt_of_feeder.setdefault(feeder_id, []).append(dt_id)
        return self

    def pole(self, pid, dt_id, parent, lat, lon, device=True,
             pincode="560078", ward="W-084", ambiguous=False, fw="1.4.2"):
        d = self.g.dts[dt_id]
        self.g.poles[pid] = PoleNode(
            id=pid, lat=lat, lon=lon, dt_id=dt_id, feeder_id=d.feeder_id,
            has_device=device, pincode=pincode, ward=ward, fw=(fw if device else None),
        )
        self.g.parent[pid] = parent
        self.g.children.setdefault(pid, [])
        if parent is not None:
            self.g.children.setdefault(parent, []).append(pid)
        d.pole_ids.append(pid)
        if parent is None:
            d.roots.append(pid)
        if ambiguous:
            self.g.ambiguous.add(pid)
        return self

    def build(self) -> NetworkGraph:
        for pid in self.g.poles:
            self.g.children.setdefault(pid, [])
        return self.g


def line_dt(builder: GraphBuilder, dt_id, feeder_id, n=4, **dtkw):
    """A straight line DT -> P{dt}_1 -> ... -> P{dt}_n. Returns pole ids."""
    builder.dt(dt_id, feeder_id, **dtkw)
    ids = []
    parent = None
    lat, lon = 12.9700, 77.5900
    for i in range(1, n + 1):
        pid = f"{dt_id}-P{i}"
        lat += 0.0003
        builder.pole(pid, dt_id, parent, lat, lon)
        parent = pid
        ids.append(pid)
    return ids

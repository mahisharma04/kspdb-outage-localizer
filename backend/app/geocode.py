"""PIN-code resolution.

~3% of poles have no pincode in the registry. Rather than depend on any hosted
geocoder (which would break for a reviewer with no API key), we fill gaps from
the network itself: the PIN of the nearest pole that *does* have one. This is
fully offline and always available. The UI marks any PIN that was filled this
way so the operator knows it is approximate.
"""
from __future__ import annotations

from .geo import haversine_m
from .topology import NetworkGraph


class PinResolver:
    def __init__(self, graph: NetworkGraph) -> None:
        self._known = [
            (p.lat, p.lon, p.pincode)
            for p in graph.poles.values() if p.pincode
        ]

    def nearest(self, lat: float, lon: float) -> tuple[str | None, bool]:
        """Return (pincode, was_filled). was_filled=True means approximate."""
        if not self._known:
            return None, False
        best, bestd = None, float("inf")
        for plat, plon, pin in self._known:
            d = haversine_m(lat, lon, plat, plon)
            if d < bestd:
                bestd, best = d, pin
        return best, True

"""Small geographic helpers. No external deps — a hand-rolled haversine is
plenty accurate at city scale and keeps the image small."""
from __future__ import annotations

import math

EARTH_R_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def midpoint(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Good-enough midpoint at city scale (linear average of coordinates)."""
    return (lat1 + lat2) / 2.0, (lon1 + lon2) / 2.0

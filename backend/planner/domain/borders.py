"""A rough US land border, to keep a domestic trip's split points inside the country.

Long trips are split at points taken from a car route (OSRM), which happily runs
through Canada (Detroit -> Buffalo via Ontario) and so would drag the truck route
across two border crossings. The outlines below are simplified (a few dozen points),
plenty to tell "this split point is in Canada or Mexico" for points hundreds of
miles apart; they are not for anything finer than that.
"""
from __future__ import annotations

from bisect import bisect_right

from .geo import LatLon

# The southern edge of Canada, west to east, as (lon, lat).
CANADA_SOUTH = [  # longitudes strictly increasing (a few vertical segments repeat one)
    (-125.0, 48.4), (-123.3, 48.3), (-123.0, 49.0), (-95.15, 49.0), (-94.8, 48.7), (-93.0, 48.6),
    (-89.6, 48.0), (-88.4, 48.3), (-84.8, 46.9), (-84.1, 46.5), (-83.4, 45.9), (-82.6, 42.5),
    (-82.5, 41.7), (-79.8, 42.6), (-79.05, 43.0), (-76.4, 44.1), (-74.7, 45.0), (-71.5, 45.0),
    (-70.0, 46.4), (-69.2, 47.45), (-68.3, 47.35), (-67.8, 47.06), (-67.8, 45.6), (-67.0, 44.9),
]

# The northern edge of Mexico, west to east, as (lon, lat).
MEXICO_NORTH = [
    (-117.1, 32.53), (-114.7, 32.72), (-111.0, 31.33), (-108.2, 31.33), (-108.2, 31.78),
    (-106.53, 31.74), (-106.4, 31.7), (-104.5, 29.6), (-103.0, 29.0), (-101.4, 29.8), (-99.5, 27.5),
    (-97.15, 25.95),
]

MARGIN_DEG = 0.5  # how far inside the US a misplaced point is moved


def _edge_lat(edge: list[tuple[float, float]], lon: float) -> float | None:
    """The border's latitude at `lon` (linear between outline points), or None off its ends."""
    lons = [p[0] for p in edge]
    if not lons[0] <= lon <= lons[-1]:
        return None
    k = max(1, min(bisect_right(lons, lon), len(edge) - 1))
    (x0, y0), (x1, y1) = edge[k - 1], edge[k]
    return y0 if x1 == x0 else y0 + (lon - x0) / (x1 - x0) * (y1 - y0)


def in_canada(p: LatLon) -> bool:
    lat = _edge_lat(CANADA_SOUTH, p[1])
    return lat is not None and p[0] > lat


def in_mexico(p: LatLon) -> bool:
    lat = _edge_lat(MEXICO_NORTH, p[1])
    return lat is not None and p[0] < lat


def outside_us(p: LatLon) -> bool:
    return in_canada(p) or in_mexico(p)


def into_us(p: LatLon, margin: float = MARGIN_DEG) -> LatLon:
    """`p` itself if it's in the US, else the point `margin` degrees inside the border at the same longitude."""
    if in_canada(p):
        return (_edge_lat(CANADA_SOUTH, p[1]) - margin, p[1])
    if in_mexico(p):
        return (_edge_lat(MEXICO_NORTH, p[1]) + margin, p[1])
    return p


def foreign_stretch(coords: list[LatLon], sample_every: int = 20) -> tuple[int, int] | None:
    """(first, last) vertex indices of the longest run outside the US, or None if there's none."""
    best, start = None, None
    idx = list(range(0, len(coords), sample_every)) + [len(coords) - 1]
    for k in idx + [None]:
        out = k is not None and outside_us(coords[k])
        if out and start is None:
            start = k
        if not out and start is not None:
            end = prev
            if best is None or end - start > best[1] - best[0]:
                best = (start, end)
            start = None
        if k is not None:
            prev = k
    return best


VIA_MARGINS_DEG = (0.5, 1.2, 2.0)  # just inside first; further in if that's a lake


def vias_inside_us(coords: list[LatLon], stretch: tuple[int, int]) -> list[LatLon]:
    """Points inside the US level with the middle of a foreign stretch, nearest the border first.

    Routing through one makes the engine find the domestic road around (e.g. Lake Erie's
    south shore). Just inside the border can be water (Lake Erie at Sudbury's longitude), so
    callers try the next one if a point has no road nearby.
    """
    mid = coords[(stretch[0] + stretch[1]) // 2]
    return [into_us(mid, m) for m in VIA_MARGINS_DEG]

"""Small geometry helpers. Coordinates are (lat, lon) tuples in degrees."""
from __future__ import annotations

import bisect
import math
from collections.abc import Sequence

import numpy as np

LatLon = tuple[float, float]

EARTH_RADIUS_MI = 3958.8


def haversine_mi(a: LatLon, b: LatLon) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(h))


def bearing_deg(a: LatLon, b: LatLon) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def destination(origin: LatLon, bearing: float, distance_mi: float) -> LatLon:
    """Point reached from `origin` after `distance_mi` along `bearing` (degrees)."""
    lat1, lon1 = map(math.radians, origin)
    brg = math.radians(bearing)
    d = distance_mi / EARTH_RADIUS_MI
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180


def decode_polyline(encoded: str, precision: int = 6) -> list[LatLon]:
    """Decode a Google-style encoded polyline (Valhalla uses precision 6)."""
    coords: list[LatLon] = []
    index = lat = lon = 0
    factor = 10**precision
    while index < len(encoded):
        for axis in (0, 1):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))
    return coords


def cumulative_miles(coords: Sequence[LatLon]) -> list[float]:
    out = [0.0]
    for a, b in zip(coords, coords[1:]):
        out.append(out[-1] + haversine_mi(a, b))
    return out


def point_at(coords: Sequence[LatLon], cum: Sequence[float], mile: float) -> LatLon:
    """Interpolated point `mile` miles along the line."""
    if mile <= 0:
        return coords[0]
    if mile >= cum[-1]:
        return coords[-1]
    i = bisect.bisect_right(cum, mile)
    seg = cum[i] - cum[i - 1]
    t = 0.0 if seg == 0 else (mile - cum[i - 1]) / seg
    (lat1, lon1), (lat2, lon2) = coords[i - 1], coords[i]
    return lat1 + t * (lat2 - lat1), lon1 + t * (lon2 - lon1)


def interpolate(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    """Piecewise-linear y at x; xs must be ascending."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x)
    span = xs[i] - xs[i - 1]
    t = 0.0 if span == 0 else (x - xs[i - 1]) / span
    return ys[i - 1] + t * (ys[i] - ys[i - 1])


def thin_by_distance(cum_mi: Sequence[float], step_mi: float) -> list[int]:
    """Indices of vertices about `step_mi` apart along the line (always the first and last).

    Uses the line's cumulative miles, so it's one vectorised pass instead of a
    distance computation per vertex.
    """
    cum = np.asarray(cum_mi)
    if len(cum) <= 2 or step_mi <= 0:
        return list(range(len(cum)))
    bucket = np.floor(cum / step_mi)
    keep = np.flatnonzero(np.diff(bucket, prepend=-1) > 0)
    idx = keep.tolist()
    if idx[-1] != len(cum) - 1:
        idx.append(len(cum) - 1)
    return idx

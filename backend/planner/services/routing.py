"""Truck routes from Valhalla (OpenStreetMap data, truck costing with vehicle weight, no API key).

Two things the public Valhalla server doesn't do for us:
- It refuses requests whose locations are more than ~1,500 km apart in total, so
  long trips are split into shorter requests and joined. The split points come
  from OSRM (a free car router with no distance limit): vertices of its route, so
  they sit on real interstates, never on a mountain pass a straight line crosses.
  If a split point still sits just off the natural road, the truck would drive
  out to it and back; those out-and-back stubs are cut.
- It returns at most a couple of alternates (fewer on long trips). For the rest we
  use the "penalty method": ask again while avoiding points along the middle of
  the routes we already have, so the engine finds its own best way around them.
  We never force a route through a made-up point, which gives unnatural hooks.
"""
from __future__ import annotations

import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings
from django.core.cache import cache

from ..domain.geo import LatLon, bearing_deg, decode_polyline, destination, haversine_mi, point_at, simplify
from ..domain.route import NUMBERED_ROAD, Route
from .http import UpstreamError, get_json, post_json, session

ROUTE_COUNT = 3
ROUTE_CACHE_S = 6 * 3600  # roads don't change; Valhalla has no traffic data
TRUCK_TARE_LB = 35_000  # empty tractor-trailer; Valhalla wants gross weight
MAX_GROSS_LB = 80_000  # US federal limit without a permit
LB_PER_TONNE = 2204.62

# The public server's limit is 1,500 km of straight-line distance per request; stay under it.
MAX_REQUEST_MI = 800
SPLIT_EVERY_MI = 600  # road miles between split points on a long trip
CHUNK_WORKERS = 4  # parallel leg requests; modest, it's a free public server

# An alternative is only worth showing if most of it is on different roads and it isn't absurdly long.
MAX_SHARED_FRACTION = 0.85
MAX_ALTERNATIVE_TIME_RATIO = 1.5
MAX_FILL_TIME_RATIO = 2.0  # looser, for the third slot when nothing better exists
# Penalty method rounds: which stretch of each known route to avoid, and how many points.
AVOID_ROUNDS = (((0.25, 0.75), 5), ((0.25, 0.75), 9), ((0.1, 0.5), 6), ((0.5, 0.9), 6))
MAX_SPUR_MI = 40  # longest out-and-back stub we look for at a split point


def gross_weight_tonnes(load_lb: float) -> float:
    return round(min(TRUCK_TARE_LB + load_lb, MAX_GROSS_LB) / LB_PER_TONNE, 1)


class NoRoute(ValueError):
    """There is no truck route between the two places (e.g. one is across water)."""


# Valhalla errors that mean "your places can't be connected", not "the server broke".
_NO_ROUTE_ERRORS = ("No suitable edges", "No path could be found", "Locations are in unconnected regions")


@dataclass
class _Path:
    """Shape and per-maneuver timing, joinable across several Valhalla requests."""

    coords: list[LatLon] = field(default_factory=list)
    segments: list[tuple[int, int, float]] = field(default_factory=list)
    names: Counter[str] = field(default_factory=Counter)

    def extend(self, trip: dict) -> None:
        for leg in trip["legs"]:
            offset = max(len(self.coords) - 1, 0)  # consecutive legs share their joining vertex
            shape = decode_polyline(leg["shape"], precision=6)
            self.coords.extend(shape if not self.coords else shape[1:])
            for m in leg.get("maneuvers", []):
                self.segments.append((m["begin_shape_index"] + offset, m["end_shape_index"] + offset, m["time"]))
                names = m.get("street_names", [])
                numbered = [n for n in names if NUMBERED_ROAD.match(n)]
                for name in (numbered or names)[:1]:
                    self.names[name] += m.get("length", 0)

    def to_route(self) -> Route:
        roads = _merge_directions(self.names)
        via = " / ".join(name for name, _ in roads.most_common(2))
        return Route.from_timed_segments(self.coords, self.segments, via=via, roads=dict(roads))


_DIRECTIONS = {"north", "south", "east", "west", "northbound", "southbound", "eastbound", "westbound"}


def _merge_directions(names: Counter[str]) -> Counter[str]:
    """Miles per road, with "I 20" and "I 20 East" counted as one road."""
    merged: Counter[str] = Counter()
    for name, miles in names.items():
        base = " ".join(w for w in name.split() if w.lower() not in _DIRECTIONS) or name
        merged[base] += miles
    return merged


def _location(point: LatLon, kind: str) -> dict:
    loc = {"lat": point[0], "lon": point[1], "type": kind}
    if kind == "through":
        # Snap intermediate points to a major road so splitting a long trip doesn't
        # send the truck down a county lane.
        loc["search_filter"] = {"min_road_class": "primary"}
        loc["radius"] = 5_000  # split points come from a real road, so they're close to one
    return loc


def _post(points: list[LatLon], load_lb: float, alternates: int, avoid: list[LatLon] = ()) -> list[dict]:
    kinds = ["break"] + ["through"] * (len(points) - 2) + ["break"]
    body = {
        "locations": [_location(p, k) for p, k in zip(points, kinds)],
        "costing": "truck",
        "costing_options": {"truck": {"weight": gross_weight_tonnes(load_lb)}},
        "alternates": alternates,
        "units": "miles",
        "directions_type": "maneuvers",
    }
    if avoid:
        body["exclude_locations"] = [{"lat": lat, "lon": lon} for lat, lon in avoid]
    try:
        data = post_json(session(), "routing", f"{settings.VALHALLA_URL}/route", json=body)
    except UpstreamError as exc:
        if any(e in exc.message for e in _NO_ROUTE_ERRORS):
            raise NoRoute("No truck route connects these places. Is one of them across water?") from exc
        raise
    return [data["trip"]] + [alt["trip"] for alt in data.get("alternates", [])]


def _straight_mi(points: list[LatLon]) -> float:
    return sum(haversine_mi(a, b) for a, b in zip(points, points[1:]))


def osrm_skeletons(origin: LatLon, dest: LatLon) -> list[list[LatLon]]:
    """Car routes from OSRM as lists of road vertices, best first (empty if OSRM is down)."""
    url = f"{settings.OSRM_URL}/route/v1/driving/{origin[1]},{origin[0]};{dest[1]},{dest[0]}"
    params = {"alternatives": 3, "overview": "simplified", "geometries": "geojson"}
    try:
        data = get_json(session(), "routing", url, params=params)
    except UpstreamError:
        return []
    return [[(lat, lon) for lon, lat in r["geometry"]["coordinates"]] for r in data.get("routes", [])]


def split_points(skeleton: list[LatLon]) -> list[LatLon]:
    """Origin, a road vertex every ~SPLIT_EVERY_MI, destination."""
    points = [skeleton[0]]
    since = 0.0
    for a, b in zip(skeleton, skeleton[1:]):
        since += haversine_mi(a, b)
        if since >= SPLIT_EVERY_MI and b != skeleton[-1]:
            points.append(b)
            since = 0.0
    points.append(skeleton[-1])
    return points


def _chunks(points: list[LatLon]) -> list[list[LatLon]]:
    """Split a list of waypoints into requests that each stay under MAX_REQUEST_MI.

    Callers pass split points on real roads for long trips. Any hop still too long
    (e.g. OSRM was down) gets evenly spaced great-circle points as a fallback,
    which Valhalla snaps to the nearest major road.
    """
    dense: list[LatLon] = [points[0]]
    for a, b in zip(points, points[1:]):
        hop = haversine_mi(a, b)
        pieces = math.ceil(hop / MAX_REQUEST_MI)
        heading = bearing_deg(a, b)
        dense.extend(destination(a, heading, hop * i / pieces) for i in range(1, pieces))
        dense.append(b)

    chunks, current = [], [dense[0]]
    for p in dense[1:]:
        if len(current) > 1 and _straight_mi(current + [p]) > MAX_REQUEST_MI:
            chunks.append(current)
            current = [current[-1]]
        current.append(p)
    chunks.append(current)
    return chunks


def _near_chunk(avoid: list[LatLon], chunk: list[LatLon], margin_mi: float = 100) -> list[LatLon]:
    """The avoid points that matter for one chunk of a split trip."""
    span = _straight_mi(chunk)
    return [p for p in avoid if min(haversine_mi(p, c) for c in chunk) <= span / 2 + margin_mi]


def request_routes(
    points: list[LatLon], load_lb: float, alternates: int, avoid: list[LatLon] = ()
) -> list[Route]:
    """The route through `points` (plus alternates when the trip fits in one request)."""
    chunks = _chunks(points)
    if len(chunks) == 1:
        routes = []
        for trip in _post(points, load_lb, alternates, list(avoid)):
            path = _Path()
            path.extend(trip)
            routes.append(path.to_route())
        return routes
    # The legs don't depend on each other: fetch them in parallel, then join in order.
    with ThreadPoolExecutor(max_workers=CHUNK_WORKERS) as pool:
        trips = list(pool.map(lambda c: _post(c, load_lb, 0, _near_chunk(list(avoid), c))[0], chunks))
    path = _Path()
    joints = []
    for trip in trips:
        if path.coords:
            joints.append(len(path.coords) - 1)
        path.extend(trip)
    return [remove_spurs(path.to_route(), joints)]


def _spur_at(route: Route, k: int) -> tuple[int, int] | None:
    """(i, j) with i < k < j if the route drives out to vertex k and comes back to where it was."""
    lo = k
    while lo > 0 and route.cum_mi[k] - route.cum_mi[lo - 1] <= MAX_SPUR_MI:
        lo -= 1
    hi = k
    while hi < len(route.coords) - 1 and route.cum_mi[hi + 1] - route.cum_mi[k] <= MAX_SPUR_MI:
        hi += 1
    # Index the outbound side on a fine grid, then find the earliest inbound match.
    grid: dict[tuple[int, int], int] = {}
    for i in range(lo, k):
        lat, lon = route.coords[i]
        grid.setdefault((int(lat * 500), int(lon * 500)), i)  # ~0.14 mi cells
    best = None
    for j in range(hi, k, -1):  # farthest return point first = biggest stub
        lat, lon = route.coords[j]
        r, c = int(lat * 500), int(lon * 500)
        hits = [grid[(r + dr, c + dc)] for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (r + dr, c + dc) in grid]
        if hits:
            i = min(hits)
            if route.cum_mi[j] - route.cum_mi[i] > 0.5 and (best is None or i < best[0]):
                best = (i, j)
    return best


def remove_spurs(route: Route, joints: list[int]) -> Route:
    """Cut out-and-back stubs at the points where a split trip was joined."""
    for k in sorted(joints, reverse=True):  # back to front, so earlier indices stay valid
        if (span := _spur_at(route, k)) is not None:
            route = route.without(*span)
    return route


# --- Picking three distinct routes ------------------------------------------


class _Footprint:
    """Grid cells a route passes through, for a quick "same road?" check."""

    CELL_DEG = 0.02  # about 1.4 mi

    def __init__(self, route: Route):
        self.cells = {self._cell(p) for p in simplify(route.coords, 0.5)}

    def _cell(self, p: LatLon) -> tuple[int, int]:
        return int(p[0] // self.CELL_DEG), int(p[1] // self.CELL_DEG)

    def covers(self, p: LatLon) -> bool:
        r, c = self._cell(p)
        return any((r + dr, c + dc) in self.cells for dr in (-1, 0, 1) for dc in (-1, 0, 1))


def _shared_fraction(candidate: Route, footprints: list[_Footprint]) -> float:
    """Share of the candidate's length that runs along one of the routes we already have."""
    step = max(candidate.distance_mi / 200, 1.0)
    miles = [i * step for i in range(int(candidate.distance_mi // step) + 1)]
    pts = [point_at(candidate.coords, candidate.cum_mi, m) for m in miles]
    shared = sum(1 for p in pts if any(f.covers(p) for f in footprints))
    return shared / len(pts)


def _avoid_points(routes: list[Route], span: tuple[float, float], per_route: int) -> list[LatLon]:
    """Points along a stretch of each route for the engine to route around."""
    lo, hi = span
    points = []
    for r in routes:
        for i in range(per_route):
            frac = lo + (hi - lo) * i / max(per_route - 1, 1)
            points.append(point_at(r.coords, r.cum_mi, r.distance_mi * frac))
    return points


def overlap(route: Route, others: list[Route]) -> float:
    """Share of `route` that runs along any of `others` (0-1), for the route cards."""
    return _shared_fraction(route, [_Footprint(o) for o in others]) if others else 0.0


def truck_routes(origin: LatLon, dest: LatLon, load_lb: float, departure: datetime) -> list[Route]:
    """Three truck routes (fewer only if the engine has no other way at all), best first.

    `departure` is unused: Valhalla's public server has no traffic data, so travel
    time doesn't depend on when you leave.
    """
    key = f"routes:{origin[0]:.4f},{origin[1]:.4f}:{dest[0]:.4f},{dest[1]:.4f}:{gross_weight_tonnes(load_lb)}"
    if (hit := cache.get(key)) is not None:
        return hit
    routes = _distinct_routes(origin, dest, load_lb)
    cache.set(key, routes, ROUTE_CACHE_S)
    return routes


def _first_routes(origin: LatLon, dest: LatLon, load_lb: float) -> tuple[list[Route], list[LatLon]]:
    """The first candidate routes, and the waypoints later requests should use.

    Short trips: Valhalla's own route + alternates, straight from origin to destination.
    Long trips: one truck route per OSRM skeleton, through split points on its roads;
    the main skeleton's split points are reused for the penalty-method requests.
    """
    if haversine_mi(origin, dest) <= MAX_REQUEST_MI:
        return request_routes([origin, dest], load_lb, ROUTE_COUNT - 1), [origin, dest]
    def truck_route(waypoints: list[LatLon]) -> list[Route]:
        try:
            return request_routes(waypoints, load_lb, 0)
        except UpstreamError:
            return []

    # One truck route per skeleton, built in parallel (each is several Valhalla requests).
    plans = [split_points([origin, *skeleton[1:-1], dest]) for skeleton in osrm_skeletons(origin, dest)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(truck_route, plans))
    routes = [r for found in results for r in found]
    main_waypoints = next((w for w, found in zip(plans, results) if found), None)
    if not routes:  # OSRM down: fall back to great-circle splits
        return request_routes([origin, dest], load_lb, 0), [origin, dest]
    return routes, main_waypoints


def _distinct_routes(origin: LatLon, dest: LatLon, load_lb: float) -> list[Route]:
    """The main route plus the two most different reasonable alternatives.

    Candidates come from the engine's own alternates, then penalty rounds (each
    avoids a different stretch), one round at a time until three routes are mostly
    on different roads and at most 1.5x slower. If the road network doesn't allow
    that (e.g. one interstate through the mountains), the third slot gets the
    least-overlapping candidate up to 2x slower, so there are always three.
    """
    routes, waypoints = _first_routes(origin, dest, load_lb)
    main = routes[0]
    chosen = [main]
    pool: list[Route] = []  # reasonable but not different enough, for the fill step

    def consider(candidate: Route) -> None:
        if len(chosen) >= ROUTE_COUNT or candidate.duration_s > main.duration_s * MAX_FILL_TIME_RATIO:
            return
        if candidate.duration_s <= main.duration_s * MAX_ALTERNATIVE_TIME_RATIO and (
            overlap(candidate, chosen) <= MAX_SHARED_FRACTION
        ):
            chosen.append(candidate)
        else:
            pool.append(candidate)

    for alt in routes[1:]:
        consider(alt)
    for span, per_route in AVOID_ROUNDS:
        if len(chosen) >= ROUTE_COUNT:
            break  # usually after the first round
        try:
            found = request_routes(waypoints, load_lb, 0, avoid=_avoid_points(routes, span, per_route))
        except UpstreamError:
            continue  # e.g. no truck-legal way around those points
        consider(found[0])

    while len(chosen) < ROUTE_COUNT and pool:
        pool.sort(key=lambda r: overlap(r, chosen))
        best = pool.pop(0)
        if overlap(best, chosen) < 0.995:  # skip exact duplicates
            chosen.append(best)
    return chosen

"""Trip planning in two steps, so the UI can show routes while the weather loads.

1. `create_trip`: places -> three truck routes -> timed checkpoints. Fast (seconds).
   Saved in the cache under a trip id, with an estimate of the weather download.
2. `plan_trip`: weather at every checkpoint's ETA -> risk -> recommendation, plus
   the 0-48 h departure window and the heatmap.
"""
from __future__ import annotations

import math
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.cache import cache

from ..domain.geo import haversine_mi, point_at, simplify
from ..domain.recommend import RouteScore, explain, rank, score_route, sort_key
from ..domain.risk import Assessment, assess, risk_index
from ..domain.route import Route, Sample, distinguishing_names, sample_checkpoints
from . import geocoding, routing, weather
from .corridor import pick_grid, with_points
from .geocoding import Place

HEATMAP_HOURS = 48
WINDOW_HOURS = 48  # also try leaving up to this many hours later
ROUTE_LETTERS = "ABC"
TRIP_CACHE_S = 3600


class TripNotFound(LookupError):
    pass


class SamePlace(ValueError):
    pass


SAME_PLACE_MI = 1.0  # closer than this, there's no trip to plan


@dataclass(frozen=True)
class TripRequest:
    origin: str | dict
    destination: str | dict
    departure: datetime  # timezone-aware
    load_lb: float
    interval_mi: int


@dataclass(frozen=True)
class TimedCheckpoint:
    """A checkpoint plus everything about it that doesn't depend on the departure time."""

    sample: Sample
    elapsed_s: float  # driving time from departure to this checkpoint
    cell: weather.Cell  # forecast grid cell


@dataclass(frozen=True)
class Trip:
    """Everything from step 1 that step 2 needs."""

    id: str
    req: TripRequest
    origin: Place
    destination: Place
    routes: dict[str, Route]
    names: dict[str, str]  # what sets each route apart, e.g. "I 40 / I 44"
    trip_s: dict[str, float]  # departure to arrival
    timed: dict[str, list[TimedCheckpoint]]
    cell_deg: float
    cells: frozenset[weather.Cell]

    @property
    def weather_start(self) -> datetime:
        return weather.floor_hour(self.req.departure)

    @property
    def weather_hours(self) -> int:
        """Enough forecast for the latest departure in the window plus the longest trip."""
        last = self.req.departure + timedelta(hours=WINDOW_HOURS, seconds=max(self.trip_s.values()))
        return math.ceil((last - self.weather_start).total_seconds() / 3600) + 1


# --- Step 1: routes ---------------------------------------------------------


def _timed(route: Route, interval_mi: int, cell_deg: float) -> list[TimedCheckpoint]:
    return [
        TimedCheckpoint(s, s.seconds, weather.snap(s.point, cell_deg)) for s in sample_checkpoints(route, interval_mi)
    ]


def create_trip(req: TripRequest) -> tuple[Trip, dict]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        origin_f = pool.submit(geocoding.resolve, req.origin)
        dest_f = pool.submit(geocoding.resolve, req.destination)
        origin, dest = origin_f.result(), dest_f.result()
    if haversine_mi((origin.lat, origin.lon), (dest.lat, dest.lon)) < SAME_PLACE_MI:
        raise SamePlace("Origin and destination are the same place. Pick two different places.")

    found = routing.truck_routes((origin.lat, origin.lon), (dest.lat, dest.lon), req.load_lb, req.departure)
    routes = dict(zip(ROUTE_LETTERS, found))
    trip_s = {rid: r.duration_s for rid, r in routes.items()}
    cell_deg, cells = pick_grid(list(routes.values()))
    timed = {rid: _timed(r, req.interval_mi, cell_deg) for rid, r in routes.items()}
    cells = with_points(cells, (t.sample.point for cps in timed.values() for t in cps), cell_deg)

    names = dict(zip(routes, distinguishing_names(list(routes.values()))))
    trip = Trip(
        uuid.uuid4().hex, req, origin, dest, routes, names, trip_s, timed, cell_deg, frozenset(cells)
    )
    cache.set(f"trip:{trip.id}", trip, TRIP_CACHE_S)
    return trip, _trip_summary(trip)


def load_trip(trip_id: str) -> Trip:
    trip = cache.get(f"trip:{trip_id}")
    if trip is None:
        raise TripNotFound("This trip has expired. Plan it again.")
    return trip


def _display_coords(route: Route) -> list[list[float]]:
    """[lon, lat] pairs thinned to ~3000 points; plenty for a web map at any zoom."""
    step = max(route.distance_mi / 3000, 0.05)
    return [[round(lon, 5), round(lat, 5)] for lat, lon in simplify(route.coords, step)]


def _route_summary(trip: Trip, rid: str) -> dict:
    route = trip.routes[rid]
    return {
        "id": rid,
        "name": f"Route {rid}",
        "via": trip.names[rid],
        "distance_mi": round(route.distance_mi, 1),
        "hours": round(route.duration_s / 3600, 2),
        "arrival": (trip.req.departure + timedelta(seconds=route.duration_s)).isoformat(),
        "geometry": {"type": "LineString", "coordinates": _display_coords(route)},
    }


def _trip_summary(trip: Trip) -> dict:
    return {
        "id": trip.id,
        "origin": trip.origin.as_dict(),
        "destination": trip.destination.as_dict(),
        "departure": trip.req.departure.isoformat(),
        "load_lb": trip.req.load_lb,
        "interval_mi": trip.req.interval_mi,
        "routes": [_route_summary(trip, rid) for rid in trip.routes],
        "weather": {
            "points": len(trip.cells),
            "estimated_s": weather.estimate_seconds(set(trip.cells), trip.weather_start, trip.weather_hours),
        },
    }


# --- Step 2: weather, risk and the recommendation -----------------------------


class RiskTable:
    """Risk per (forecast cell, forecast hour), computed on first use and then reused.

    Many (route, departure, checkpoint) combinations land in the same cell at the
    same hour (overlapping routes, and every departure delay re-reads the same
    forecasts), so each one is assessed only once.
    """

    def __init__(self, fcs: dict[weather.Cell, weather.Forecast], start: datetime, load_lb: float):
        self.fcs, self.start, self.load_lb = fcs, start, load_lb
        self._memo: dict[tuple[weather.Cell, int], Assessment | None] = {}

    def hour_index(self, when: datetime) -> int:
        return round((when - self.start).total_seconds() / 3600)

    def conditions(self, cell: weather.Cell, when: datetime) -> weather.Conditions | None:
        fc = self.fcs.get(cell)  # missing if that cell's download failed
        return None if fc is None else fc.at_index(self.hour_index(when))

    def at(self, cell: weather.Cell, when: datetime) -> Assessment | None:
        key = (cell, self.hour_index(when))
        if key not in self._memo:
            cond = self.conditions(cell, when)
            self._memo[key] = None if cond is None else assess(cond, self.load_lb)
        return self._memo[key]


def _assess_all(timed: list[TimedCheckpoint], table: RiskTable, depart_at: datetime) -> list[Assessment | None]:
    """Risk at each checkpoint for a truck leaving at `depart_at` (None = no forecast)."""
    return [table.at(t.cell, depart_at + timedelta(seconds=t.elapsed_s)) for t in timed]


def _score(rid: str, timed: list[TimedCheckpoint], assessed: list[Assessment | None], total_s: float) -> RouteScore:
    """`total_s` is the time criterion: waiting (if any) plus the trip, i.e. time until arrival."""
    legs = [(None if a is None else a.level, t.sample.covers_mi) for t, a in zip(timed, assessed)]
    return score_route(rid, legs, total_s)


def _checkpoint_dicts(
    timed: list[TimedCheckpoint], assessed: list[Assessment | None], table: RiskTable, departure: datetime
) -> list[dict]:
    out = []
    for t, a in zip(timed, assessed):
        s = t.sample
        eta = departure + timedelta(seconds=t.elapsed_s)
        cp = {
            "mile": round(s.mile, 1),
            "covers": (round(s.start_mi, 2), round(s.end_mi, 2)),
            "lat": round(s.point[0], 5),
            "lon": round(s.point[1], 5),
            "eta": eta.isoformat(),
            "hours_from_departure": round(t.elapsed_s / 3600, 2),
        }
        cond = table.conditions(t.cell, eta)
        if a is None or cond is None:
            cp.update(risk=None, risk_label="No forecast", reasons=["no forecast data for this stretch"])
        else:
            cp.update(
                risk=a.level.key,
                risk_label=a.level.label,
                reasons=list(a.reasons),
                weather={
                    "wind_mph": round(cond.wind_mph, 1),
                    "gust_mph": None if cond.gust_mph is None else round(cond.gust_mph, 1),
                    "rain_in_hr": round(cond.rain_in_hr, 3),
                    "snow_in_hr": round(cond.snow_in_hr, 3),
                },
            )
        out.append(cp)
    return out


def _risk_segments(route: Route, checkpoints: list[dict]) -> list[dict]:
    """Route pieces coloured by the risk of the checkpoint covering them (merged runs)."""
    segments: list[dict] = []
    for cp in checkpoints:
        start, end = cp["covers"]
        if segments and segments[-1]["risk"] == cp["risk"]:
            segments[-1]["to_mi"] = end
        else:
            segments.append({"risk": cp["risk"], "from_mi": start, "to_mi": end})
    for seg in segments:
        step = max((seg["to_mi"] - seg["from_mi"]) / 400, 0.1)
        n = max(int((seg["to_mi"] - seg["from_mi"]) / step), 1)
        pts = [point_at(route.coords, route.cum_mi, seg["from_mi"] + i * step) for i in range(n)]
        pts.append(point_at(route.coords, route.cum_mi, seg["to_mi"]))
        seg["coordinates"] = [[round(lon, 5), round(lat, 5)] for lat, lon in pts]
    return segments


def _score_summary(score: RouteScore, assessed: list[Assessment | None]) -> dict:
    levels = [a.level for a in assessed if a is not None]
    return {
        "miles_by_risk": {lvl.key: round(mi, 1) for lvl, mi in score.miles_by_level.items()},
        "no_forecast_mi": round(score.unknown_mi, 1),
        "severe_mi": round(score.severe_mi, 1),
        "high_mi": round(score.high_mi, 1),
        "avg_risk": round(score.avg_risk, 2),
        "max_risk": max(levels).key if levels else None,
    }


def _departure_window(trip: Trip, table: RiskTable) -> dict | None:
    """The safest (route, departure) from +0 h to +WINDOW_HOURS, if it beats leaving on time.

    Waiting counts as time, so between two equally safe options the earlier
    arrival wins; but safety always comes first, as in the normal ranking.
    Returns None when leaving at the requested time is already the safest choice.
    """
    dep = trip.req.departure
    candidates: list[tuple[int, RouteScore]] = []
    for delay_h in range(WINDOW_HOURS + 1):
        depart_at = dep + timedelta(hours=delay_h)
        for rid, cps in trip.timed.items():
            score = _score(rid, cps, _assess_all(cps, table, depart_at), delay_h * 3600 + trip.trip_s[rid])
            candidates.append((delay_h, score))

    best_now = min((sc for d, sc in candidates if d == 0), key=sort_key)
    delay_h, best = min(candidates, key=lambda c: sort_key(c[1]))
    if delay_h == 0 or sort_key(best)[:-1] >= sort_key(best_now)[:-1]:  # safer, not just faster
        return None
    return {
        "route_id": best.route_id,
        "delay_h": delay_h,
        "departure": (dep + timedelta(hours=delay_h)).isoformat(),
        "arrival": (dep + timedelta(seconds=best.duration_s)).isoformat(),
        "no_travel_mi": round(best.no_travel_mi, 1),
        "severe_mi": round(best.severe_mi, 1),
        "high_mi": round(best.high_mi, 1),
        "reason": explain(best, best_now).replace("on the next option", "if you leave at your chosen time"),
    }


def _heatmap(trip: Trip, table: RiskTable) -> dict:
    """Risk index (0-4) per cell for each hour 0..48 after departure (None = no data)."""
    dep = trip.req.departure
    ordered = sorted(trip.cells)
    values = []
    for cell in ordered:
        row = []
        for h in range(HEATMAP_HOURS + 1):
            cond = table.conditions(cell, dep + timedelta(hours=h))
            row.append(None if cond is None else round(risk_index(cond, trip.req.load_lb), 2))
        values.append(row)
    return {
        "hours": [(dep + timedelta(hours=h)).isoformat() for h in range(HEATMAP_HOURS + 1)],
        "cell_deg": trip.cell_deg,
        "points": [[lon, lat] for lat, lon in ordered],
        "values": values,
    }


def _warnings(trip: Trip, result: weather.WeatherResult) -> list[dict]:
    if not result.missing:
        return []
    why = "the free weather service's rate limit" if result.rate_limited else "a weather service error"
    return [
        {
            "kind": "weather_partial",
            "missing_points": len(result.missing),
            "total_points": len(trip.cells),
            "rate_limited": result.rate_limited,
            "message": (
                f"Weather is missing for {len(result.missing)} of {len(trip.cells)} points because of {why}. "
                "Those stretches are grey and count against a route. Try again in about a minute for full data."
            ),
        }
    ]


def plan_trip(trip: Trip) -> dict:
    result = weather.forecasts(set(trip.cells), trip.weather_start, trip.weather_hours)
    table = RiskTable(result.forecasts, trip.weather_start, trip.req.load_lb)
    dep = trip.req.departure

    routes, scores = [], []
    for rid, route in trip.routes.items():
        assessed = _assess_all(trip.timed[rid], table, dep)
        score = _score(rid, trip.timed[rid], assessed, trip.trip_s[rid])
        scores.append(score)
        checkpoints = _checkpoint_dicts(trip.timed[rid], assessed, table, dep)
        routes.append(
            {
                **_route_summary(trip, rid),
                "summary": _score_summary(score, assessed),
                "segments": _risk_segments(route, checkpoints),
                "checkpoints": checkpoints,
            }
        )

    ranked = rank(scores)
    best = ranked[0]
    no_safe_route = best.no_travel_mi > 0
    for r in routes:
        r["rank"] = next(i for i, s in enumerate(ranked) if s.route_id == r["id"]) + 1
        r["recommended"] = r["id"] == best.route_id and not no_safe_route

    return {
        **_trip_summary(trip),
        "routes": routes,
        "recommendation": {
            "route_id": None if no_safe_route else best.route_id,
            "reason": explain(best, ranked[1] if len(ranked) > 1 else None),
            "no_safe_route": no_safe_route,
        },
        "best_window": _departure_window(trip, table),
        "heatmap": _heatmap(trip, table),
        "warnings": _warnings(trip, result),
    }

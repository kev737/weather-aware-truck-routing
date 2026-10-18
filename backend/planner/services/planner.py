"""Trip planning in two steps, so the UI can show routes while the weather loads.

1. `create_trip`: places -> three truck routes -> timed checkpoints. Fast (seconds).
   Saved in the cache under a trip id, with an estimate of the weather download.
   If the first routes come back before all three are found, they're returned
   right away and the rest are found in the background (`load_trip` polls).
2. `plan_trip`: weather at every checkpoint's ETA -> risk -> recommendation, plus
   the 0-48 h departure window and the heatmap.
"""
from __future__ import annotations

import logging
import math
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.cache import cache
from django.utils import timezone

from ..domain.geo import haversine_mi, point_at, thin_by_distance
from ..domain.recommend import RouteScore, explain, rank, risk_key, score_route, sort_key
from ..domain.risk import Assessment, assess, risk_index
from ..domain.route import Route, Sample, distinguishing_names, sample_checkpoints
from . import geocoding, grids, quota, routing, weather
from .corridor import pick_grid
from .geocoding import Place

log = logging.getLogger(__name__)

HEATMAP_HOURS = 48
WINDOW_HOURS = 48  # also try leaving up to this many hours later
ROUTE_LETTERS = "ABC"
TRIP_CACHE_S = 3600
FAR_FORECAST_H = grids.HRRR_HOURS  # past this, HRRR doesn't reach: suggest checking again closer to departure


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
    cell: weather.Cell  # where its forecast is read: the checkpoint itself, to ~100 m


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
    heat_deg: float  # heatmap grid size
    heat_cells: frozenset[weather.Cell]  # heatmap grid along the routes
    points: frozenset[weather.Cell]  # every checkpoint's exact location
    routes_complete: bool = True  # False while the last routes are still being found

    @property
    def locations(self) -> frozenset[weather.Cell]:
        """Everything to download: the checkpoints and the heatmap grid."""
        return self.points | self.heat_cells

    @property
    def weather_start(self) -> datetime:
        return weather.floor_hour(self.req.departure)

    @property
    def weather_hours(self) -> int:
        """Enough forecast for the latest departure in the window plus the longest trip."""
        later = max(WINDOW_HOURS, HEATMAP_HOURS)  # the latest departure tried, or the heatmap's last hour
        last = self.req.departure + timedelta(hours=later, seconds=max(self.trip_s.values()))
        return math.ceil((last - self.weather_start).total_seconds() / 3600) + 1


# --- Step 1: routes ---------------------------------------------------------


def _timed(route: Route, interval_mi: int) -> list[TimedCheckpoint]:
    """Checkpoints every `interval_mi`, each read at its own location (the spec's sampling)."""
    return [TimedCheckpoint(s, s.seconds, weather.exact(s.point)) for s in sample_checkpoints(route, interval_mi)]


def create_trip(req: TripRequest) -> tuple[Trip, dict]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        origin_f = pool.submit(geocoding.resolve, req.origin)
        dest_f = pool.submit(geocoding.resolve, req.destination)
        origin, dest = origin_f.result(), dest_f.result()
    if haversine_mi((origin.lat, origin.lon), (dest.lat, dest.lon)) < SAME_PLACE_MI:
        raise SamePlace("Origin and destination are the same place. Pick two different places.")

    found, finish = routing.start_routes((origin.lat, origin.lon), (dest.lat, dest.lon), req.load_lb)
    trip = _build_trip(uuid.uuid4().hex, req, origin, dest, found, complete=finish is None)
    cache.set(f"trip:{trip.id}", trip, TRIP_CACHE_S)
    if finish is not None:
        in_background(lambda: _finish_routes(trip, finish))
    return trip, trip_summary(trip)


def _build_trip(trip_id: str, req: TripRequest, origin: Place, dest: Place, found: list[Route], complete: bool) -> Trip:
    routes = dict(zip(ROUTE_LETTERS, found))
    trip_s = {rid: r.duration_s for rid, r in routes.items()}
    timed = {rid: _timed(r, req.interval_mi) for rid, r in routes.items()}
    points = frozenset(t.cell for cps in timed.values() for t in cps)
    heat_deg, heat_cells = pick_grid(list(routes.values()))
    names = dict(zip(routes, distinguishing_names(list(routes.values()))))
    return Trip(trip_id, req, origin, dest, routes, names, trip_s, timed, heat_deg, frozenset(heat_cells), points, complete)


def in_background(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


def _finish_routes(trip: Trip, finish) -> None:
    """Find the remaining routes and save the full trip under the same id."""
    try:
        found = finish()
    except Exception:  # keep the routes we have rather than leave the browser waiting
        log.exception("finding the remaining routes failed for trip %s", trip.id)
        found = list(trip.routes.values())
    full = _build_trip(trip.id, trip.req, trip.origin, trip.destination, found, complete=True)
    cache.set(f"trip:{trip.id}", full, TRIP_CACHE_S)


def load_trip(trip_id: str) -> Trip:
    trip = cache.get(f"trip:{trip_id}")
    if trip is None:
        raise TripNotFound("This trip has expired. Plan it again.")
    return trip


def _display_coords(route: Route) -> list[list[float]]:
    """[lon, lat] pairs thinned to ~3000 points; plenty for a web map at any zoom."""
    step = max(route.distance_mi / 3000, 0.05)
    return [[round(route.coords[k][1], 5), round(route.coords[k][0], 5)] for k in thin_by_distance(route.cum_mi, step)]


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


def trip_summary(trip: Trip) -> dict:
    return {
        "id": trip.id,
        "origin": trip.origin.as_dict(),
        "destination": trip.destination.as_dict(),
        "departure": trip.req.departure.isoformat(),
        "load_lb": trip.req.load_lb,
        "interval_mi": trip.req.interval_mi,
        "routes": [_route_summary(trip, rid) for rid in trip.routes],
        "routes_complete": trip.routes_complete,
        "weather": {
            "points": len(trip.locations),
            "estimated_s": weather.estimate_seconds(set(trip.locations), trip.weather_start, trip.weather_hours),
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
    legs = [
        (None, t.sample.covers_mi) if a is None else (a.level, t.sample.covers_mi, (a.wind, a.rain, a.snow))
        for t, a in zip(timed, assessed)
    ]
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
                    "source": table.fcs[t.cell].source_at(table.hour_index(eta)),
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
        "risk_min": round(score.risk_min),  # weather risk as minutes of driving it's worth avoiding
        "score_h": round(score.score_h, 2),  # travel hours + risk: what the recommendation minimises
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
    if delay_h == 0 or risk_key(best) >= risk_key(best_now):  # safer, not just faster
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
    ordered = sorted(trip.heat_cells)
    values = []
    for cell in ordered:
        row = []
        for h in range(HEATMAP_HOURS + 1):
            cond = table.conditions(cell, dep + timedelta(hours=h))
            row.append(None if cond is None else round(risk_index(cond, trip.req.load_lb), 2))
        values.append(row)
    return {
        "hours": [(dep + timedelta(hours=h)).isoformat() for h in range(HEATMAP_HOURS + 1)],
        "cell_deg": trip.heat_deg,
        "points": [[lon, lat] for lat, lon in ordered],
        "values": values,
    }


def _warnings(trip: Trip, result: weather.WeatherResult) -> list[dict]:
    warnings = []
    if result.deferred:
        n, wait = len(result.deferred), result.retry_after_s
        checkpoints_waiting = len(result.deferred & trip.points)
        if checkpoints_waiting:
            what = (
                f"Weather for {n} more points arrives in about {wait} s: the weather service allows "
                f"{quota.minute_limit_text()}. Until then those stretches are grey and the recommendation is provisional."
            )
        else:  # every checkpoint is in: the recommendation is final, only the heatmap is filling in
            what = (
                f"The heatmap's last {n} points arrive in about {wait} s (the weather service allows "
                f"{quota.minute_limit_text()}). The recommendation is final."
            )
        warnings.append(
            {
                "kind": "weather_partial",
                "missing_points": n,
                "checkpoints_waiting": checkpoints_waiting,
                "total_points": len(trip.locations),
                "rate_limited": True,
                "retry": True,
                "retry_after_s": wait,
                "message": what,
            }
        )
    if result.missing:
        if result.quota_exhausted:
            why, then = "today's free weather quota running out", "Try again tomorrow for full data."
        elif result.rate_limited:
            why, then = "the free weather service's rate limit", "Filling in the rest automatically."
        else:
            why, then = "a weather service error", "Try again in a minute for full data."
        warnings.append(
            {
                "kind": "weather_partial",
                "missing_points": len(result.missing),
                "total_points": len(trip.locations),
                "rate_limited": result.rate_limited,
                "retry": result.rate_limited and not result.quota_exhausted,
                "message": (
                    f"Weather is missing for {len(result.missing)} of {len(trip.locations)} points because of {why}. "
                    f"Those stretches are grey and count against a route. {then}"
                ),
            }
        )
    if trip.req.departure > timezone.now() + timedelta(hours=FAR_FORECAST_H):
        warnings.append(
            {
                "kind": "far_forecast",
                "message": (
                    f"This departure is more than {FAR_FORECAST_H} hours away, beyond the storm-resolving HRRR "
                    f"forecast: the weather comes from GFS and is less certain. Recheck within {FAR_FORECAST_H} "
                    "hours of departure to confirm the weather holds."
                ),
            }
        )
    usage = quota.status()
    if usage["near_daily_limit"] and not result.quota_exhausted:
        warnings.append(
            {
                "kind": "quota_low",
                "message": f"Today's free weather quota is {usage['day']['pct']}% used; it resets tomorrow.",
            }
        )
    return warnings


def plan_trip(trip: Trip) -> dict:
    # Checkpoints first (they decide the ranking), route by route along the road; the heatmap last.
    priority = [t.cell for rid in trip.routes for t in trip.timed[rid]]
    result = weather.forecasts(set(trip.locations), trip.weather_start, trip.weather_hours, priority)
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
    checkpoint_cells = {t.cell for cps in trip.timed.values() for t in cps}
    provisional = bool((result.missing | result.deferred) & checkpoint_cells)
    for r in routes:
        r["rank"] = next(i for i, s in enumerate(ranked) if s.route_id == r["id"]) + 1
        r["recommended"] = r["id"] == best.route_id and not no_safe_route

    return {
        **trip_summary(trip),
        "routes": routes,
        "recommendation": {
            "route_id": None if no_safe_route else best.route_id,
            "reason": explain(best, ranked[1] if len(ranked) > 1 else None),
            "no_safe_route": no_safe_route,
            "provisional": provisional,  # some checkpoints still have no forecast
        },
        "best_window": _departure_window(trip, table),
        "window_hours": WINDOW_HOURS,  # how far ahead leaving later was tried
        "heatmap": _heatmap(trip, table),
        "warnings": _warnings(trip, result),
        "quota": quota.status(),
    }

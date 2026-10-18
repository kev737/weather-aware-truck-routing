"""Hourly forecasts from Open-Meteo (HRRR + GFS over the US; many locations per request).

Checkpoints are fetched at their own location (rounded to ~100 m, so overlapping
routes share a lookup); the heatmap uses a grid along the routes. Every location is
cached, so re-planning the same trip or switching back to an earlier spacing
doesn't hit the API again. Works on the free tier; a paid key lifts its limits.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.cache import cache

from ..domain.geo import LatLon
from ..domain.risk import Conditions
from . import quota
from .http import RateLimited, UpstreamError, get_json, session

HOURLY_VARS = ("wind_speed_10m", "wind_gusts_10m", "rain", "showers", "snowfall")
BATCH_SIZE = 50  # locations per request; smaller batches come back faster
MAX_WORKERS = 4
RETRY_WAIT_S = (1, 3)  # quick retries only; the browser backs off longer (proxy timeouts are ~26 s)
# Measured: a batch of 50 locations takes ~1 s plus ~4 s per 120 forecast hours.
BATCH_BASE_S = 1.0
BATCH_PER_HOUR_S = 4 / 120

Cell = tuple[float, float]


def exact(point: LatLon) -> Cell:
    """A point rounded to ~100 m: its own forecast, shared only by points that close."""
    return round(point[0], 3), round(point[1], 3)


def snap(point: LatLon, cell_deg: float) -> Cell:
    """Centre of the grid cell containing `point`."""
    lat = (point[0] // cell_deg) * cell_deg + cell_deg / 2
    lon = (point[1] // cell_deg) * cell_deg + cell_deg / 2
    return round(lat, 4), round(lon, 4)


def floor_hour(t: datetime) -> datetime:
    return t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class Forecast:
    """One cell's hourly series starting at `start` (UTC, on the hour)."""

    start: datetime
    wind: tuple[float | None, ...]
    gust: tuple[float | None, ...]
    rain: tuple[float | None, ...]  # rain + showers, in/hr
    snow: tuple[float | None, ...]  # in/hr
    source: tuple[str | None, ...] = ()  # model per hour ("hrrr", "gfs", "open-meteo")

    def source_at(self, i: int) -> str | None:
        return self.source[i] if 0 <= i < len(self.source) else "open-meteo"

    def at_index(self, i: int) -> Conditions | None:
        if not 0 <= i < len(self.wind):
            return None
        wind, rain, snow = self.wind[i], self.rain[i], self.snow[i]
        if wind is None or rain is None or snow is None:
            return None  # beyond the model's horizon
        return Conditions(wind_mph=wind, rain_in_hr=rain, snow_in_hr=snow, gust_mph=self.gust[i])

    def at(self, when: datetime) -> Conditions | None:
        """Conditions for the forecast hour nearest `when`."""
        return self.at_index(round((when - self.start).total_seconds() / 3600))


def _cache_key(cell: Cell, start: datetime, hours: int) -> str:
    return f"wx:{cell[0]}:{cell[1]}:{start:%Y%m%d%H}:{hours}"


def _parse(item: dict, start: datetime) -> Forecast:
    h = item["hourly"]
    rain = tuple(
        None if r is None or s is None else r + s for r, s in zip(h["rain"], h["showers"])
    )
    return Forecast(
        start=start,
        wind=tuple(h["wind_speed_10m"]),
        gust=tuple(h["wind_gusts_10m"]),
        rain=rain,
        snow=tuple(h["snowfall"]),
    )


def _fetch_batch(cells: list[Cell], start: datetime, hours: int) -> list[Forecast]:
    end = start + timedelta(hours=hours - 1)
    params = {
        "latitude": ",".join(str(c[0]) for c in cells),
        "longitude": ",".join(str(c[1]) for c in cells),
        "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "GMT",
        "start_hour": f"{start:%Y-%m-%dT%H:%M}",
        "end_hour": f"{end:%Y-%m-%dT%H:%M}",
    }
    if settings.OPEN_METEO_API_KEY:
        params["apikey"] = settings.OPEN_METEO_API_KEY
    for wait in (*RETRY_WAIT_S, None):
        try:
            data = get_json(session(), "weather", settings.OPEN_METEO_URL, params=params, timeout=45)
            break
        except RateLimited as exc:
            limit_hit = quota.from_provider_error(exc.message)
            if isinstance(limit_hit, quota.QuotaExhausted) or wait is None:
                raise limit_hit from exc  # retrying can't fix a daily or hourly limit
            time.sleep(wait)
        except UpstreamError:
            if wait is None:
                raise
            time.sleep(wait)
    items = data if isinstance(data, list) else [data]
    if len(items) != len(cells):
        raise UpstreamError("weather", "forecast count does not match the request")
    return [_parse(item, start) for item in items]


@dataclass(frozen=True)
class WeatherResult:
    forecasts: dict[Cell, Forecast]
    missing: frozenset[Cell]  # cells whose download failed
    rate_limited: bool  # at least one failure was the API's rate limit
    quota_exhausted: bool = False  # a daily/hourly limit: retrying soon won't help
    deferred: frozenset[Cell] = frozenset()  # not fetched yet: this minute's budget ran out
    retry_after_s: int | None = None  # when the deferred cells can be fetched


def _uncached(cells: set[Cell], start: datetime, hours: int) -> list[Cell]:
    return [c for c in sorted(cells) if cache.get(_cache_key(c, start, hours)) is None]


def estimate_seconds(cells: set[Cell], start: datetime, hours: int) -> int:
    """Rough time to download what isn't cached yet, for the progress bar."""
    batches = math.ceil(len(_uncached(cells, start, hours)) / BATCH_SIZE)
    rounds = math.ceil(batches / MAX_WORKERS)  # batches run MAX_WORKERS at a time
    return math.ceil(rounds * (BATCH_BASE_S + BATCH_PER_HOUR_S * hours))


def _from_grids(grids, cell: Cell, start: datetime, hours: int) -> Forecast | None:
    """A cell's forecast read from the local NOAA grids, or None outside them."""
    series = grids.hourly(cell[0], cell[1], start, hours)
    if series is None:
        return None
    pick = lambda k: tuple(None if v is None else v[k] for v in series)  # noqa: E731
    gust = tuple(None if v is None or math.isnan(v[1]) else v[1] for v in series)
    return Forecast(start=start, wind=pick(0), gust=gust, rain=pick(2), snow=pick(3), source=pick(4))


def forecasts(
    cells: set[Cell], start: datetime, hours: int, priority: list[Cell] | None = None
) -> WeatherResult:
    """Hourly forecasts for every cell: from the local NOAA grids where they reach
    (no limits), from Open-Meteo for the rest (paced to its free tier)."""
    from .grids import Grids

    grids = Grids.current() if settings.USE_NOAA_GRIDS else None
    if grids is None or not grids.available():
        return _open_meteo_forecasts(cells, start, hours, priority)
    local = {c: fc for c in cells if (fc := _from_grids(grids, c, start, hours)) is not None}
    rest = set(cells) - set(local)
    if not rest:
        return WeatherResult(local, frozenset(), rate_limited=False)
    remote = _open_meteo_forecasts(rest, start, hours, [c for c in (priority or []) if c in rest])
    return WeatherResult(
        {**local, **remote.forecasts},
        remote.missing,
        rate_limited=remote.rate_limited,
        quota_exhausted=remote.quota_exhausted,
        deferred=remote.deferred,
        retry_after_s=remote.retry_after_s,
    )


def _open_meteo_forecasts(
    cells: set[Cell], start: datetime, hours: int, priority: list[Cell] | None = None
) -> WeatherResult:
    """Hourly forecasts for every cell, `hours` long from `start` (UTC hour).

    Cells are fetched in `priority` order (then the rest), and only as many as the
    free tier's per-minute budget allows right now; the others come back as
    `deferred` with the seconds until they can be fetched. The caller shows what it
    has and asks again; cached cells are never fetched twice. A failed batch doesn't
    fail the trip either: its cells are reported as missing. Only when nothing at
    all is available do we raise.
    """
    out: dict[Cell, Forecast] = {}
    for cell in cells:
        if (hit := cache.get(_cache_key(cell, start, hours))) is not None:
            out[cell] = hit
    first = [c for c in (priority or []) if c in cells and c not in out]
    todo = list(dict.fromkeys(first + [c for c in sorted(cells) if c not in out]))
    if todo and not out:
        quota.check_daily(len(todo))  # nothing to show anyway: say so before trying
    budget = quota.minute_budget()
    todo, deferred = todo[:budget], todo[budget:]
    if deferred and not todo and not out:
        wait = quota.seconds_until_free()
        raise RateLimited("weather", f"the weather service allows {quota.minute_limit_text()}; retrying in {wait} s", wait)
    batches = [todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]

    missing: set[Cell] = set()
    errors: list[UpstreamError] = []

    def fetch(batch: list[Cell]) -> None:
        try:
            fcs = _fetch_batch(batch, start, hours)
        except UpstreamError as exc:
            missing.update(batch)
            errors.append(exc)
            return
        for cell, fc in zip(batch, fcs):
            out[cell] = fc
            cache.set(_cache_key(cell, start, hours), fc, settings.WEATHER_CACHE_S)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(fetch, batches))
    # Counted once here, not in the worker threads: the cache's incr isn't atomic.
    quota.record(len(todo) - len(missing))

    if cells and not out:
        raise errors[0]
    return WeatherResult(
        out,
        frozenset(missing),
        rate_limited=any(isinstance(e, RateLimited) for e in errors),
        quota_exhausted=any(isinstance(e, quota.QuotaExhausted) for e in errors),
        deferred=frozenset(deferred),
        retry_after_s=quota.seconds_until_free() if deferred else None,
    )

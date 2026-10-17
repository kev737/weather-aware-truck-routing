"""Hourly forecasts from Open-Meteo (no API key, many locations per request).

Points are snapped to a grid before fetching. Nearby checkpoints share one
forecast (weather models are 3-13 km resolution anyway), routes that overlap
reuse each other's data, and each cell is cached, so changing the checkpoint
interval or re-planning the same trip doesn't hit the API again.
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
from .http import RateLimited, UpstreamError, get_json, session

HOURLY_VARS = ("wind_speed_10m", "wind_gusts_10m", "rain", "showers", "snowfall")
BATCH_SIZE = 50  # locations per request; smaller batches come back faster
MAX_WORKERS = 4
RETRY_WAIT_S = (1, 3)  # quick retries only; the browser backs off longer (proxy timeouts are ~26 s)
# Measured: a batch of 50 locations takes ~1 s plus ~4 s per 120 forecast hours.
BATCH_BASE_S = 1.0
BATCH_PER_HOUR_S = 4 / 120

Cell = tuple[float, float]


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


def _uncached(cells: set[Cell], start: datetime, hours: int) -> list[Cell]:
    return [c for c in sorted(cells) if cache.get(_cache_key(c, start, hours)) is None]


def estimate_seconds(cells: set[Cell], start: datetime, hours: int) -> int:
    """Rough time to download what isn't cached yet, for the progress bar."""
    batches = math.ceil(len(_uncached(cells, start, hours)) / BATCH_SIZE)
    rounds = math.ceil(batches / MAX_WORKERS)  # batches run MAX_WORKERS at a time
    return math.ceil(rounds * (BATCH_BASE_S + BATCH_PER_HOUR_S * hours))


def forecasts(cells: set[Cell], start: datetime, hours: int) -> WeatherResult:
    """Hourly forecasts for every cell, `hours` long from `start` (UTC hour).

    A failed batch doesn't fail the trip: its cells are reported as missing and
    shown as "no forecast". Only when nothing at all could be fetched do we raise.
    """
    out: dict[Cell, Forecast] = {}
    for cell in cells:
        if (hit := cache.get(_cache_key(cell, start, hours))) is not None:
            out[cell] = hit
    todo = [c for c in sorted(cells) if c not in out]
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

    if cells and not out:
        raise errors[0]
    return WeatherResult(out, frozenset(missing), any(isinstance(e, RateLimited) for e in errors))

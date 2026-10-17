from datetime import datetime, timezone

import pytest
from django.core.cache import cache

from planner.services import weather
from planner.services.http import RateLimited

START = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def fake_forecast(start, hours):
    calm = (5.0,) * hours
    zero = (0.0,) * hours
    return weather.Forecast(start=start, wind=calm, gust=calm, rain=zero, snow=zero)


def test_snap_returns_cell_centres():
    assert weather.snap((41.88, -87.63), 0.1) == (41.85, -87.65)
    assert weather.snap((41.81, -87.69), 0.1) == (41.85, -87.65)  # same cell


def test_retries_only_fetch_what_is_still_missing(monkeypatch):
    """A failed batch is reported as missing; the next call downloads only those cells."""
    monkeypatch.setattr(weather, "BATCH_SIZE", 2)
    cells = {(40.05, -100.05), (40.15, -100.05), (40.25, -100.05), (40.35, -100.05)}
    requested: list[list] = []
    fail_first = {(40.25, -100.05), (40.35, -100.05)}
    attempt = {"n": 0}

    def fetch(batch, start, hours):
        requested.append(list(batch))
        if attempt["n"] == 0 and set(batch) & fail_first:
            raise RateLimited("weather", "slow down")
        return [fake_forecast(start, hours) for _ in batch]

    monkeypatch.setattr(weather, "_fetch_batch", fetch)

    first = weather.forecasts(cells, START, 24)
    assert first.missing == fail_first and first.rate_limited
    assert set(first.forecasts) == cells - fail_first

    attempt["n"] = 1
    requested.clear()
    second = weather.forecasts(cells, START, 24)
    assert not second.missing
    assert set(second.forecasts) == cells
    assert sorted(c for batch in requested for c in batch) == sorted(fail_first)  # additive


def test_total_failure_raises(monkeypatch):
    def fetch(batch, start, hours):
        raise RateLimited("weather", "slow down")

    monkeypatch.setattr(weather, "_fetch_batch", fetch)
    with pytest.raises(RateLimited):
        weather.forecasts({(40.05, -100.05)}, START, 24)


def test_estimate_counts_only_uncached_cells(monkeypatch):
    monkeypatch.setattr(weather, "_fetch_batch", lambda b, s, h: [fake_forecast(s, h) for _ in b])
    cells = {(40.0 + i / 10, -100.05) for i in range(120)}  # 3 batches of 50 -> 1 round of 4 workers
    assert weather.estimate_seconds(cells, START, 120) == 5  # 1 s + 4 s for 120 hours
    weather.forecasts(cells, START, 120)
    assert weather.estimate_seconds(cells, START, 120) == 0


def test_conditions_at_hour_and_beyond_horizon():
    fc = fake_forecast(START, 3)
    assert fc.at_index(2).wind_mph == 5.0
    assert fc.at_index(3) is None


def test_suggestions_rank_us_towns_first_and_label_counties(monkeypatch):
    from planner.services import geocoding

    def feature(name, kind, state, cc, lat=29.4, lon=-98.5):
        props = {"name": name, "type": kind, "state": state, "countrycode": cc}
        return {"properties": props, "geometry": {"coordinates": [lon, lat]}}

    photon = {
        "features": [
            feature("San Anto", "district", "Cajamarca", "PE"),
            feature("San Anto Cultural Arts Center", "house", "Texas", "US"),
            feature("San Antonio", "city", "Texas", "US"),
            feature("Dallas", "county", "Texas", "US"),
        ]
    }
    monkeypatch.setattr(geocoding, "get_json", lambda *a, **k: photon)
    names = [p.name for p in geocoding.search("San Anto")]
    assert names[0] == "San Antonio, Texas, US"
    assert "Dallas County, Texas, US" in names

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from planner.services import quota, weather
from planner.services.http import RateLimited

NOW = datetime(2026, 10, 18, 15, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean(settings):
    settings.OPEN_METEO_LIMITS = {"minute": 600, "hour": 5000, "day": 1000}
    cache.clear()
    yield
    cache.clear()


def test_usage_is_counted_per_minute_hour_and_day():
    quota.record(120, NOW)
    quota.record(30, NOW + timedelta(minutes=2))  # same hour and day, new minute
    assert quota.used(NOW) == {"minute": 120, "hour": 150, "day": 150}
    assert quota.used(NOW + timedelta(days=1))["day"] == 0  # a new day starts at zero


def test_status_warns_near_the_daily_limit():
    quota.record(790, NOW)
    assert quota.status(NOW)["near_daily_limit"] is False
    quota.record(20, NOW)
    s = quota.status(NOW)
    assert s["near_daily_limit"] is True and s["day"]["pct"] == 81
    assert s["day"]["resets"].startswith("2026-10-19")


def test_a_download_that_would_cross_the_daily_limit_is_refused():
    quota.record(950, NOW)
    quota.check_daily(50, NOW)  # exactly at the limit is fine
    with pytest.raises(quota.QuotaExhausted, match="try again tomorrow"):
        quota.check_daily(51, NOW)


@pytest.mark.parametrize(
    "reason, exhausted, text",
    [
        ("Daily API request limit exceeded. Please try again tomorrow.", True, "tomorrow"),
        ("Hourly API request limit exceeded.", True, "in an hour"),
        ("Minutely API request limit exceeded.", False, "retrying shortly"),
    ],
)
def test_provider_errors_say_which_limit_was_hit(reason, exhausted, text):
    err = quota.from_provider_error(reason)
    assert isinstance(err, RateLimited)
    assert isinstance(err, quota.QuotaExhausted) is exhausted
    assert text in err.message


def test_weather_fetch_counts_locations_and_stops_at_the_daily_limit(monkeypatch):
    start = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    calm = weather.Forecast(start, (5.0,) * 24, (5.0,) * 24, (0.0,) * 24, (0.0,) * 24)
    monkeypatch.setattr(weather, "_fetch_batch", lambda b, s, h: [calm] * len(b))
    cells = {(40.0 + i / 10, -100.05) for i in range(60)}
    weather.forecasts(cells, start, 24)
    assert quota.used()["day"] == 60

    quota.record(1000)  # someone else used the rest of today's quota
    fresh = {(30.0 + i / 10, -90.05) for i in range(10)}
    with pytest.raises(quota.QuotaExhausted):
        weather.forecasts(fresh, start, 24)


def test_exhausted_quota_is_a_503_without_retry(monkeypatch):
    from planner.services import planner

    def exhausted(trip):
        raise quota.QuotaExhausted(quota.daily_message())

    monkeypatch.setattr(planner, "load_trip", lambda trip_id: SimpleNamespace(routes_complete=True))
    monkeypatch.setattr(planner, "plan_trip", exhausted)
    resp = APIClient().get("/api/trips/abc/plan/")
    assert resp.status_code == 503
    body = resp.json()
    assert body["quota_exhausted"] is True
    assert "retry_after_s" not in body
    assert body["detail"].startswith("Today's free weather quota")


def test_usage_endpoint():
    quota.record(42)
    body = APIClient().get("/api/usage/").json()
    assert body["day"]["used"] == 42 and body["day"]["limit"] == 1000


def test_minute_budget_counts_only_the_last_60_seconds(settings):
    settings.OPEN_METEO_LIMITS = {"minute": 600, "hour": 5000, "day": 1000}
    t = datetime(2026, 10, 18, 15, 30, 50, tzinfo=timezone.utc)
    full = quota.per_minute()
    quota.record(500, t)
    assert quota.minute_budget(t + timedelta(seconds=15)) == full - 500  # still inside the window
    assert quota.seconds_until_free(t + timedelta(seconds=15)) == 46  # 61 s after the burst
    assert quota.minute_budget(t + timedelta(seconds=62)) == min(full, 1000 - 500)  # free again, capped by the day


def test_fetch_takes_priority_cells_first_and_defers_the_rest(monkeypatch):
    start = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    calm = weather.Forecast(start, (5.0,) * 24, (5.0,) * 24, (0.0,) * 24, (0.0,) * 24)
    fetched: list = []

    def fetch(batch, s, h):
        fetched.extend(batch)
        return [calm] * len(batch)

    monkeypatch.setattr(weather, "_fetch_batch", fetch)
    monkeypatch.setattr(quota, "minute_budget", lambda now=None: 3)
    checkpoints = [(40.001, -100.001), (40.002, -100.002), (40.003, -100.003)]
    heat = {(41.05, -101.05), (41.15, -101.05)}
    res = weather.forecasts(set(checkpoints) | heat, start, 24, priority=checkpoints)
    assert sorted(fetched) == sorted(checkpoints)  # the budget went to the checkpoints
    assert res.deferred == frozenset(heat) and res.retry_after_s is not None


def test_no_budget_at_all_is_a_precise_retry(monkeypatch):
    start = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(quota, "minute_budget", lambda now=None: 0)
    with pytest.raises(RateLimited) as err:
        weather.forecasts({(40.0, -100.0)}, start, 24)
    assert 1 <= err.value.retry_after_s <= 61

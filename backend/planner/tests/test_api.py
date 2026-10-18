"""End-to-end through the API with the third-party services faked."""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from planner.domain.geo import destination
from planner.domain.route import Route
from planner.services import planner, routing, weather
from planner.services.geocoding import Place

HOUR = 3600
ORIGIN = (35.0, -100.0)
DESTINATION = (37.7, -97.3)  # the fake routes don't use it; it just isn't the origin


def fake_resolve(value):
    return Place(str(value), *(ORIGIN if value == "Amarillo, TX" else DESTINATION))


def line(bearing: float, miles: float, mph: float) -> Route:
    coords = [destination(ORIGIN, bearing, miles * i / 50) for i in range(51)]
    return Route.from_timed_segments(coords, [(0, 50, miles / mph * HOUR)])


# Route A heads north-east (fast), B north (slower), C north-west (slowest).
ROUTES = [line(45, 300, 60), line(0, 320, 58), line(315, 330, 55)]


def result(fcs, missing=(), rate_limited=False):
    return weather.WeatherResult(fcs, frozenset(missing), rate_limited)


class FakeForecast:
    """Storm wind (50 mph) everywhere east of -100.2 longitude (the origin is in it), calm elsewhere."""

    def __init__(self, cell):
        self.windy = cell[1] > -100.2

    def source_at(self, i):
        return "hrrr"

    def at_index(self, i):
        if not 0 <= i <= 60:
            return None
        return weather.Conditions(wind_mph=50 if self.windy else 5, rain_in_hr=0, snow_in_hr=0, gust_mph=60)


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr("planner.services.geocoding.resolve", fake_resolve)
    monkeypatch.setattr(routing, "start_routes", lambda o, d, load: (ROUTES, None))
    monkeypatch.setattr(weather, "forecasts", lambda cells, start, hours, priority=None: result({c: FakeForecast(c) for c in cells}))


def body(**overrides):
    return {
        "origin": "Amarillo, TX",
        "destination": "Wichita, KS",
        "departure": (timezone.now() + timedelta(hours=2)).isoformat(),
        "load_lb": 20_000,
        "interval_mi": 25,
        **overrides,
    }


def post(data):
    """Both steps: create the trip (routes), then fetch its plan (weather + risk)."""
    client = APIClient()
    created = client.post("/api/trips/", data, format="json")
    if created.status_code != 201:
        return created
    return client.get(f"/api/trips/{created.json()['id']}/plan/")


def test_plan_recommends_the_route_away_from_the_storm(fakes):
    resp = post(body())
    assert resp.status_code == 200
    data = resp.json()
    assert [r["id"] for r in data["routes"]] == ["A", "B", "C"]
    a = data["routes"][0]
    assert a["summary"]["severe_mi"] > 0  # the fast route drives through 50 mph wind
    assert data["recommendation"]["route_id"] == "C"  # leaves the storm soonest
    assert "avoids weather worth" in data["recommendation"]["reason"]


def test_heavy_load_turns_severe_wind_into_no_travel(fakes):
    data = post(body(load_lb=35_000)).json()
    a = data["routes"][0]
    assert a["summary"]["miles_by_risk"]["no_travel"] > 0
    assert any("load over 30,000 lb" in r for cp in a["checkpoints"] for r in cp["reasons"])


def test_checkpoint_spacing_follows_the_interval(fakes):
    for interval in (10, 25, 50):
        cps = post(body(interval_mi=interval)).json()["routes"][0]["checkpoints"]
        assert cps[1]["mile"] == interval
        assert cps[-1]["mile"] == 300


def test_response_has_map_and_heatmap_data(fakes):
    data = post(body()).json()
    r = data["routes"][0]
    assert r["geometry"]["type"] == "LineString"
    assert {s["risk"] for s in r["segments"]} <= {"low", "moderate", "high", "severe", "no_travel"}
    heat = data["heatmap"]
    assert len(heat["hours"]) == 49  # 0..48 h after departure
    assert len(heat["values"]) == len(heat["points"])
    assert all(len(row) == 49 for row in heat["values"])


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"interval_mi": 15}, "interval_mi"),
        ({"load_lb": -1}, "load_lb"),
        ({"load_lb": 90_000}, "load_lb"),
        ({"origin": ""}, "origin"),
        ({"departure": "2020-01-01T00:00:00Z"}, "departure"),
        ({"departure": (timezone.now() + timedelta(days=30)).isoformat()}, "departure"),
    ],
)
def test_validation_errors(fakes, overrides, field):
    resp = post(body(**overrides))
    assert resp.status_code == 400
    assert field in resp.json()


def test_upstream_failure_is_a_clear_502(monkeypatch, fakes):
    def boom(*args):
        raise routing.UpstreamError("routing", "no truck route found between these places")

    monkeypatch.setattr(routing, "start_routes", boom)
    resp = post(body())
    assert resp.status_code == 502
    assert "no truck route" in resp.json()["detail"]


class PassingStorm:
    """50 mph wind everywhere until `ends`, calm afterwards. Hour 0 is `start`."""

    def __init__(self, start, ends):
        self.start, self.ends = start, ends

    def source_at(self, i):
        return "hrrr"

    def at_index(self, i):
        windy = self.start + timedelta(hours=i) < self.ends
        return weather.Conditions(wind_mph=50 if windy else 5, rain_in_hr=0, snow_in_hr=0, gust_mph=None)


def storm_until(monkeypatch, departure, hours):
    ends = departure + timedelta(hours=hours)
    monkeypatch.setattr(weather, "forecasts", lambda cells, start, h, priority=None: result({c: PassingStorm(start, ends) for c in cells}))


def test_waiting_out_a_storm_is_suggested(monkeypatch, fakes):
    on_the_hour = (timezone.now() + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    req = body(load_lb=35_000, departure=on_the_hour.isoformat())  # heavy: 50 mph wind is No Travel
    storm_until(monkeypatch, on_the_hour, hours=5)
    data = post(req).json()

    assert data["recommendation"]["no_safe_route"] is True
    assert data["recommendation"]["route_id"] is None
    assert not any(r["recommended"] for r in data["routes"])

    window = data["best_window"]
    assert window["delay_h"] == 5  # the first departure that misses the storm entirely
    assert window["no_travel_mi"] == 0
    assert window["route_id"] == "A"  # all routes are clear then, so the fastest wins


def test_no_window_when_waiting_does_not_help(fakes):
    # The storm in `fakes` never moves, so leaving later is never safer.
    assert post(body()).json()["best_window"] is None



def test_step_one_returns_routes_and_a_weather_estimate(fakes, monkeypatch):
    monkeypatch.setattr(weather, "estimate_seconds", lambda cells, start, hours: 12)
    resp = APIClient().post("/api/trips/", body(), format="json")
    assert resp.status_code == 201
    data = resp.json()
    assert [r["id"] for r in data["routes"]] == ["A", "B", "C"]
    assert data["weather"]["estimated_s"] == 12
    assert data["weather"]["points"] > 0
    assert "summary" not in data["routes"][0]  # risk comes in step 2


def test_first_routes_come_back_at_once_and_the_last_one_follows(fakes, monkeypatch):
    later = []
    monkeypatch.setattr(routing, "start_routes", lambda o, d, load: (ROUTES[:2], lambda: ROUTES))
    monkeypatch.setattr(planner, "in_background", later.append)  # run it when the test says
    client = APIClient()
    first = client.post("/api/trips/", body(), format="json").json()
    assert [r["id"] for r in first["routes"]] == ["A", "B"] and first["routes_complete"] is False
    assert client.get(f"/api/trips/{first['id']}/plan/").status_code == 409  # no weather until all three

    later.pop()()  # the background search finishes
    polled = client.get(f"/api/trips/{first['id']}/").json()
    assert [r["id"] for r in polled["routes"]] == ["A", "B", "C"] and polled["routes_complete"] is True
    assert client.get(f"/api/trips/{first['id']}/plan/").status_code == 200


def test_if_the_background_search_fails_the_first_routes_are_kept(fakes, monkeypatch):
    def broken():
        raise routing.UpstreamError("routing", "server went away")

    later = []
    monkeypatch.setattr(routing, "start_routes", lambda o, d, load: (ROUTES[:2], broken))
    monkeypatch.setattr(planner, "in_background", later.append)
    client = APIClient()
    trip_id = client.post("/api/trips/", body(), format="json").json()["id"]
    later.pop()()
    polled = client.get(f"/api/trips/{trip_id}/").json()
    assert len(polled["routes"]) == 2 and polled["routes_complete"] is True


def test_unknown_trip_is_404(fakes):
    assert APIClient().get("/api/trips/nope/").status_code == 404
    assert APIClient().get("/api/trips/nope/plan/").status_code == 404


def test_partial_weather_is_reported_and_never_helps_a_route(monkeypatch, fakes):
    def partial(cells, start, hours, priority=None):
        # Route C (north-west) loses its weather; the storm in FakeForecast hits A and B.
        missing = {c for c in cells if c[1] < -100.2}
        return result({c: FakeForecast(c) for c in cells if c not in missing}, missing, rate_limited=True)

    monkeypatch.setattr(weather, "forecasts", partial)
    data = post(body()).json()
    warning = data["warnings"][0]
    assert warning["kind"] == "weather_partial" and warning["rate_limited"] is True
    c = next(r for r in data["routes"] if r["id"] == "C")
    assert c["summary"]["no_forecast_mi"] > 0
    assert any(cp["risk"] is None for cp in c["checkpoints"])
    # C has no storm data at all, but unknown miles rank right after No Travel,
    # so it must not win just because its weather is missing.
    assert data["recommendation"]["route_id"] != "C"


def test_rate_limit_with_no_data_is_a_503_with_retry_after(monkeypatch, fakes):
    from planner.services.http import RateLimited

    def limited(*args):
        raise RateLimited("weather", "the free tier's rate limit was reached")

    monkeypatch.setattr(weather, "forecasts", limited)
    resp = post(body())
    assert resp.status_code == 503
    assert resp.json()["retry_after_s"] == 60


def test_same_origin_and_destination_is_rejected(fakes):
    resp = APIClient().post("/api/trips/", body(destination="Amarillo, TX"), format="json")
    assert resp.status_code == 400
    assert "same place" in resp.json()["detail"]


def test_unroutable_places_are_a_clear_400(monkeypatch, fakes):
    def no_route(*args):
        raise routing.NoRoute("No truck route connects these places. Is one of them across water?")

    monkeypatch.setattr(routing, "start_routes", no_route)
    resp = APIClient().post("/api/trips/", body(destination="Honolulu, HI"), format="json")
    assert resp.status_code == 400
    assert "across water" in resp.json()["detail"]


@pytest.mark.parametrize("interval", [10, 25, 50])
def test_checkpoints_are_read_at_their_own_location(monkeypatch, fakes, interval):
    """The spec's sampling: each checkpoint's forecast is fetched at that checkpoint, not a grid cell."""
    asked: set = set()

    def record(cells, start, hours, priority=None):
        asked.update(cells)
        return result({c: FakeForecast(c) for c in cells})

    monkeypatch.setattr(weather, "forecasts", record)
    data = post(body(interval_mi=interval)).json()
    for cp in data["routes"][0]["checkpoints"]:
        assert (round(cp["lat"], 3), round(cp["lon"], 3)) in asked


def test_deferred_weather_makes_the_recommendation_provisional(monkeypatch, fakes):
    def last_checkpoint_later(cells, start, hours, priority=None):
        later = {priority[-1]}  # the last checkpoint didn't fit in this minute's budget
        return weather.WeatherResult(
            {c: FakeForecast(c) for c in cells - later}, frozenset(), True, deferred=frozenset(later), retry_after_s=37
        )

    monkeypatch.setattr(weather, "forecasts", last_checkpoint_later)
    data = post(body()).json()
    assert data["recommendation"]["provisional"] is True
    w = next(w for w in data["warnings"] if w["kind"] == "weather_partial")
    assert w["retry"] is True and w["retry_after_s"] == 37
    assert "arrives in about 37 s" in w["message"]


def test_deferring_only_heatmap_cells_keeps_the_recommendation_final(monkeypatch, fakes):
    def heat_later(cells, start, hours, priority=None):
        later = set(cells) - set(priority)  # every checkpoint arrived; only heatmap cells wait
        return weather.WeatherResult(
            {c: FakeForecast(c) for c in set(priority)}, frozenset(), True, deferred=frozenset(later), retry_after_s=20
        )

    monkeypatch.setattr(weather, "forecasts", heat_later)
    assert post(body()).json()["recommendation"]["provisional"] is False


def test_heatmap_only_wait_says_the_recommendation_is_final(monkeypatch, fakes):
    def heat_later(cells, start, hours, priority=None):
        later = set(cells) - set(priority)
        return weather.WeatherResult(
            {c: FakeForecast(c) for c in set(priority)}, frozenset(), True, deferred=frozenset(later), retry_after_s=20
        )

    monkeypatch.setattr(weather, "forecasts", heat_later)
    w = next(w for w in post(body()).json()["warnings"] if w["kind"] == "weather_partial")
    assert w["checkpoints_waiting"] == 0
    assert "recommendation is final" in w["message"] and "provisional" not in w["message"]

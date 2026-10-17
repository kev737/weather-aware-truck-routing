from planner.domain.recommend import explain, rank, risk_key, score_route
from planner.domain.risk import Risk

L, M, H, S, N = Risk.LOW, Risk.MODERATE, Risk.HIGH, Risk.SEVERE, Risk.NO_TRAVEL
HOUR = 3600


def ids(scores):
    return [s.route_id for s in rank(scores)]


def test_score_is_travel_time_plus_weather_minutes():
    s = score_route("A", [(L, 100), (M, 20), (H, 10), (S, 3)], 5 * HOUR)
    assert s.risk_min == 20 * 0.5 + 10 * 2 + 3 * 10  # 60 minutes of weather
    assert s.score_h == 6.0


def test_a_short_detour_beats_severe_weather():
    fast = score_route("A", [(L, 100), (S, 6)], 5 * HOUR)  # 60 min of risk
    detour = score_route("B", [(L, 110)], 5.5 * HOUR)  # 30 min slower, clear
    assert ids([fast, detour]) == ["B", "A"]


def test_a_huge_detour_does_not_beat_a_little_severe_weather():
    """The point of balancing: 2 Severe miles (20 min) aren't worth 3 extra hours."""
    fast = score_route("A", [(L, 100), (S, 2)], 5 * HOUR)
    detour = score_route("B", [(L, 250)], 8 * HOUR)
    assert ids([fast, detour]) == ["A", "B"]


def test_severe_weighs_more_than_high_which_weighs_more_than_moderate():
    severe = score_route("S", [(S, 10)], 5 * HOUR)
    high = score_route("H", [(H, 10)], 5 * HOUR)
    moderate = score_route("M", [(M, 10)], 5 * HOUR)
    assert ids([severe, high, moderate]) == ["M", "H", "S"]


def test_no_travel_always_loses():
    a = score_route("A", [(L, 90), (N, 1)], 5 * HOUR)
    b = score_route("B", [(S, 200)], 40 * HOUR)  # awful, but drivable
    assert ids([a, b]) == ["B", "A"]


def test_missing_forecast_never_helps():
    a = score_route("A", [(L, 50), (None, 50)], 5 * HOUR)
    b = score_route("B", [(L, 100), (H, 10)], 6 * HOUR)
    assert ids([a, b]) == ["B", "A"]


def test_each_mile_counts_once_in_its_own_level():
    s = score_route("A", [(S, 10), (N, 5)], HOUR)
    assert (s.severe_mi, s.no_travel_mi) == (10, 5)


def test_full_tie_keeps_routing_order():
    a = score_route("A", [(L, 100)], HOUR)
    b = score_route("B", [(L, 100)], HOUR)
    assert ids([a, b]) == ["A", "B"]


def test_risk_key_ignores_travel_time():
    slow_clear = score_route("A", [(L, 100)], 9 * HOUR)
    fast_stormy = score_route("B", [(L, 100), (S, 5)], 5 * HOUR)
    assert risk_key(slow_clear) < risk_key(fast_stormy)


def test_explain_states_the_trade_off():
    slower_safer = score_route("A", [(L, 100)], 5.5 * HOUR)
    faster_stormy = score_route("B", [(L, 100), (S, 6)], 5 * HOUR)
    assert explain(slower_safer, faster_stormy) == (
        "30 min slower than the next option, but avoids weather worth 60 min (only Low miles vs 6 Severe mi)."
    )
    c = score_route("C", [(L, 100)], 5 * HOUR)
    d = score_route("D", [(L, 100)], 5.5 * HOUR)
    assert explain(c, d) == "Same weather exposure as the next option, and 30 min faster."
    e = score_route("E", [(L, 100), (N, 1)], HOUR)
    assert explain(c, e).startswith("Avoids No Travel conditions")
    assert explain(c, None) == "Only one route was available."


def test_explain_names_moderate_miles_when_they_decide():
    a = score_route("A", [(L, 100), (M, 10)], 5 * HOUR)
    b = score_route("B", [(L, 80), (M, 30)], 5 * HOUR)
    assert explain(a, b) == "Less weather risk than the next option (10 Moderate mi vs 30 Moderate mi)."

import pytest

from planner.domain.recommend import explain, rank, risk_key, score_route, weather_minutes
from planner.domain.risk import Risk

L, M, H, S, N = Risk.LOW, Risk.MODERATE, Risk.HIGH, Risk.SEVERE, Risk.NO_TRAVEL
HOUR = 3600


def ids(scores):
    return [s.route_id for s in rank(scores)]


def test_weights_come_from_crash_cost_against_truck_time():
    """$0.24 crash cost per mile / $1.50 per minute x 7 risk aversion = 1.12 min per extra "normal mile"."""
    assert weather_minutes([L]) == 0
    assert weather_minutes([M]) == pytest.approx(0.78, abs=0.01)
    assert weather_minutes([H]) == pytest.approx(2.24, abs=0.01)
    assert weather_minutes([S]) == pytest.approx(10.08, abs=0.01)


def test_hazards_on_the_same_mile_multiply():
    severe_wind_in_high_rain = weather_minutes([S, H, L])  # 10x * 3x = 30x the crash rate
    assert severe_wind_in_high_rain == pytest.approx(29 * 1.12, abs=0.1)
    assert severe_wind_in_high_rain > weather_minutes([S]) + weather_minutes([H])  # more than adding


def test_stacked_hazards_raise_the_score_but_not_the_level():
    alone = score_route("A", [(S, 10, (S, L, L))], HOUR)
    stacked = score_route("B", [(S, 10, (S, H, L))], HOUR)
    assert alone.severe_mi == stacked.severe_mi == 10
    assert stacked.risk_min == pytest.approx(alone.risk_min * 29 / 9)  # (30 - 1) / (10 - 1)
    assert ids([stacked, alone]) == ["A", "B"]


def test_score_is_travel_time_plus_weather_minutes():
    s = score_route("A", [(L, 100), (M, 20), (H, 10), (S, 3)], 5 * HOUR)
    assert s.risk_min == pytest.approx(20 * 0.784 + 10 * 2.24 + 3 * 10.08)  # about 68 minutes of weather
    assert s.score_h == pytest.approx(5 + s.risk_min / 60)


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


def test_equal_scores_fall_back_to_the_briefs_order():
    """1 Severe mile and 4.5 High miles are worth the same minutes; the brief prefers fewer Severe."""
    severe = score_route("A", [(L, 100), (S, 1)], 5 * HOUR)
    high = score_route("B", [(L, 100), (H, 4.5)], 5 * HOUR)
    assert round(severe.score_h * 60) == round(high.score_h * 60)
    assert ids([severe, high]) == ["B", "A"]
    assert explain(high, severe).startswith("Same score as the next option; wins on the brief's order")


def test_differences_under_a_minute_are_a_tie():
    a = score_route("A", [(L, 100)], 5 * HOUR + 20)  # 20 s slower: same score to the minute
    b = score_route("B", [(L, 100)], 5 * HOUR)
    assert ids([a, b]) == ["A", "B"]  # within a minute it's a tie: routing order stays


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


def test_explain_says_when_the_faster_route_carries_a_little_more_weather():
    faster = score_route("A", [(L, 100), (H, 18)], 2.65 * HOUR)  # 40 min of weather
    slower = score_route("B", [(L, 100), (H, 15)], 3.07 * HOUR)  # 34 min of weather, 25 min slower
    assert ids([faster, slower]) == ["A", "B"]
    assert explain(faster, slower) == (
        "25 min faster than the next option, worth more than its 7 min of extra weather (18 High mi vs 15 High mi)."
    )


def test_explain_names_moderate_miles_when_they_decide():
    a = score_route("A", [(L, 100), (M, 10)], 5 * HOUR)
    b = score_route("B", [(L, 80), (M, 30)], 5 * HOUR)
    assert explain(a, b) == "Less weather risk than the next option (10 Moderate mi vs 30 Moderate mi)."

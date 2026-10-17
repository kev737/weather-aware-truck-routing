from planner.domain.recommend import explain, rank, score_route
from planner.domain.risk import Risk

L, M, H, S, N = Risk.LOW, Risk.MODERATE, Risk.HIGH, Risk.SEVERE, Risk.NO_TRAVEL
HOUR = 3600


def ids(scores):
    return [s.route_id for s in rank(scores)]


def test_fewest_severe_miles_beats_faster_route():
    slow_safe = score_route("A", [(L, 100), (H, 50)], 10 * HOUR)
    fast_severe = score_route("B", [(L, 140), (S, 10)], 6 * HOUR)
    assert ids([fast_severe, slow_safe]) == ["A", "B"]


def test_then_fewest_high_miles():
    a = score_route("A", [(L, 100), (H, 20)], 5 * HOUR)
    b = score_route("B", [(M, 110), (H, 10)], 6 * HOUR)
    assert ids([a, b]) == ["B", "A"]


def test_then_lowest_average_risk():
    a = score_route("A", [(M, 100)], 5 * HOUR)
    b = score_route("B", [(L, 100)], 6 * HOUR)
    assert ids([a, b]) == ["B", "A"]


def test_then_shortest_time():
    a = score_route("A", [(L, 100)], 6 * HOUR)
    b = score_route("B", [(L, 120)], 5 * HOUR)
    assert ids([a, b]) == ["B", "A"]


def test_no_travel_is_worse_than_any_severe():
    a = score_route("A", [(L, 90), (N, 1)], 5 * HOUR)
    b = score_route("B", [(S, 200)], 9 * HOUR)
    assert ids([a, b]) == ["B", "A"]


def test_no_travel_counts_as_severe_miles():
    s = score_route("A", [(S, 10), (N, 5)], HOUR)
    assert s.severe_mi == 15


def test_sub_mile_noise_does_not_override_travel_time():
    a = score_route("A", [(L, 100), (S, 10.2)], 6 * HOUR)
    b = score_route("B", [(L, 100), (S, 10.4)], 5 * HOUR)
    assert ids([a, b]) == ["B", "A"]


def test_average_is_distance_weighted():
    s = score_route("A", [(L, 90), (N, 10)], HOUR)
    assert s.avg_risk == 0.4


def test_full_tie_keeps_routing_order():
    a = score_route("A", [(L, 100)], HOUR)
    b = score_route("B", [(L, 100)], HOUR)
    assert ids([a, b]) == ["A", "B"]


def test_explain_names_the_deciding_criterion():
    a = score_route("A", [(L, 100), (H, 5)], 6 * HOUR)
    b = score_route("B", [(L, 100), (H, 30)], 5 * HOUR)
    assert explain(a, b) == "Fewest High miles: 5 mi vs 30 mi on the next option."
    c = score_route("C", [(L, 100)], 5 * HOUR)
    d = score_route("D", [(L, 100)], 5.5 * HOUR)
    assert explain(c, d) == "Same weather exposure as the next option, and 30 min faster."
    assert explain(c, None) == "Only one route was available."

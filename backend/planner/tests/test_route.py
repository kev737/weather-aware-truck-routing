import pytest

from planner.domain.geo import decode_polyline, destination, haversine_mi
from planner.domain.route import Route, sample_checkpoints

HOUR = 3600


def straight_route(miles: float, mph: float, vertices: int = 101) -> Route:
    """A due-north line of `miles` at constant speed."""
    start = (35.0, -100.0)
    coords = [destination(start, 0, miles * i / (vertices - 1)) for i in range(vertices)]
    return Route.from_timed_segments(coords, [(0, vertices - 1, miles / mph * HOUR)])


def test_haversine_known_distance():
    # Chicago -> Denver is ~920 mi as the crow flies.
    assert haversine_mi((41.8781, -87.6298), (39.7392, -104.9903)) == pytest.approx(920, rel=0.01)


def test_decode_polyline_precision_6():
    # Valhalla's encoding of two points (lat 38.5, lon -120.2) -> (40.7, -120.95).
    encoded = "_izlhA~rlgdF_{geC~ywl@"
    assert decode_polyline(encoded) == [(38.5, -120.2), (40.7, -120.95)]


def test_time_is_spread_by_distance_within_a_segment():
    coords = [destination((35.0, -100.0), 0, m) for m in (0, 10, 30)]
    route = Route.from_timed_segments(coords, [(0, 2, 1800)])
    assert route.cum_s[1] == pytest.approx(600, rel=1e-3)  # 10 of 30 mi -> 1/3 of the time
    assert route.duration_s == 1800


def test_checkpoints_every_interval_plus_destination():
    route = straight_route(miles=105, mph=60)
    miles = [round(s.mile) for s in sample_checkpoints(route, 25)]
    assert miles == [0, 25, 50, 75, 100, 105]


def test_checkpoints_cover_the_whole_route_without_overlap():
    route = straight_route(miles=105, mph=60)
    samples = sample_checkpoints(route, 10)
    assert sum(s.covers_mi for s in samples) == pytest.approx(route.distance_mi)
    for a, b in zip(samples, samples[1:]):
        assert a.end_mi == pytest.approx(b.start_mi)


def test_checkpoint_eta_follows_speed():
    route = straight_route(miles=100, mph=50)
    s50 = next(s for s in sample_checkpoints(route, 50) if round(s.mile) == 50)
    assert s50.seconds == pytest.approx(HOUR, rel=1e-3)





def test_road_names_ignore_direction():
    from collections import Counter

    from planner.services.routing import _merge_directions

    roads = _merge_directions(Counter({"I 20": 300, "I 20 East": 200, "US 82": 400}))
    assert roads == {"I 20": 500, "US 82": 400}


def test_routes_are_named_by_what_sets_them_apart():
    from planner.domain.route import distinguishing_names

    def r(roads):
        route = straight_route(miles=10, mph=50)
        route.roads = roads
        return route

    a = r({"I 80": 900, "I 70": 400})
    b = r({"I 80": 880, "I 76": 300, "I 70": 100})
    c = r({"I 40": 1200, "I 44": 300})
    assert distinguishing_names([a, b, c]) == ["I 70", "I 76", "I 40 / I 44"]


def test_out_and_back_stub_is_cut_at_a_joint():
    from planner.services.routing import remove_spurs

    # East 30 mi, a 5 mi stub north and back (the split point), then east 30 mi more.
    east = [destination((35.0, -100.0), 90, m) for m in range(0, 31)]
    junction = east[-1]
    stub_out = [destination(junction, 0, m * 0.5) for m in range(1, 11)]
    stub_back = list(reversed(stub_out[:-1])) + [junction]
    rest = [destination(junction, 90, m) for m in range(1, 31)]
    coords = east + stub_out + stub_back + rest
    route = Route.from_timed_segments(coords, [(0, len(coords) - 1, 3 * HOUR)])
    tip = len(east) + len(stub_out) - 1  # where the two requests were joined

    cleaned = remove_spurs(route, [tip])
    assert cleaned.distance_mi == pytest.approx(60, abs=0.5)  # the 10 mi out-and-back is gone
    assert cleaned.duration_s < route.duration_s


def test_route_without_keeps_time_consistent():
    route = straight_route(miles=100, mph=50)
    cut = route.without(20, 40)
    assert len(cut.coords) == len(route.coords) - 19
    assert cut.duration_s == pytest.approx(route.duration_s - (route.cum_s[40] - route.cum_s[20]))


def test_split_points_are_skeleton_vertices_about_600_mi_apart():
    from planner.services.routing import split_points

    skeleton = [destination((35.0, -80.0), 270, m) for m in range(0, 2001, 50)]  # 2000 mi due west
    points = split_points(skeleton)
    assert points[0] == skeleton[0] and points[-1] == skeleton[-1]
    assert all(p in skeleton for p in points)  # never invents a point off the road
    assert len(points) == 5  # splits at 600, 1200, 1800 mi


def test_numbered_road_names_win_over_local_names():
    from planner.services.routing import _Path

    path = _Path()
    trip = {
        "legs": [
            {
                "shape": "_izlhA~rlgdF_{geC~ywl@",
                "maneuvers": [
                    {"begin_shape_index": 0, "end_shape_index": 1, "time": 60, "length": 50,
                     "street_names": ["Pan Am Expressway", "I 35"]},
                ],
            }
        ]
    }
    path.extend(trip)
    assert dict(path.names) == {"I 35": 50}


def test_route_names_prefer_numbered_highways():
    from planner.domain.route import distinguishing_names

    def r(roads):
        route = straight_route(miles=10, mph=50)
        route.roads = roads
        return route

    a = r({"I 35": 200, "Pan Am Expressway": 60})
    b = r({"TX 220": 150, "US 281": 120, "Main Street": 30})
    assert distinguishing_names([a, b]) == ["I 35", "TX 220 / US 281"]

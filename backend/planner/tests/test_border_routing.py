"""Domestic trips stay in the US; cross-border trips are left alone. Valhalla is faked."""
import pytest

from planner.domain.borders import foreign_stretch, outside_us, vias_inside_us
from planner.services import routing as R
from planner.services.http import UpstreamError

DETROIT, BUFFALO = (42.33, -83.05), (42.89, -78.88)
TOLEDO, CLEVELAND, ERIE = (41.65, -83.55), (41.50, -81.69), (42.13, -80.08)  # Lake Erie's south shore
TORONTO, LONDON_ON = (43.65, -79.38), (42.98, -81.25)
SEATTLE, VANCOUVER = (47.61, -122.33), (49.28, -123.12)


def encode(coords):
    """Google polyline encoding at precision 6, as Valhalla returns shapes."""
    out, prev = [], (0, 0)
    for lat, lon in coords:
        cur = (round(lat * 1e6), round(lon * 1e6))
        for value in (cur[0] - prev[0], cur[1] - prev[1]):
            v = ~(value << 1) if value < 0 else value << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev = cur
    return "".join(out)


def line(*waypoints, steps=40):
    pts = []
    for (a, b) in zip(waypoints, waypoints[1:]):
        pts += [(a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps) for k in range(steps)]
    return pts + [waypoints[-1]]


def trip(*waypoints):
    shape = line(*waypoints)
    return {"legs": [{"shape": encode(shape), "maneuvers": [{"begin_shape_index": 0, "end_shape_index": len(shape) - 1, "time": 3600, "length": 100, "street_names": ["I 90"]}]}]}


class FakeValhalla:
    """Answers through Ontario unless asked to go through a point in Ohio."""

    def __init__(self, fail_first_via=False):
        self.calls, self.fail_first_via = [], fail_first_via

    def __call__(self, points, load_lb, alternates, avoid=()):
        self.calls.append(list(points))
        vias = points[1:-1]
        if self.fail_first_via and len(self.calls) == 2:
            raise UpstreamError("routing", "No suitable edges near location")  # that via was in Lake Erie
        if any(v[0] < 42.0 for v in vias):  # routed via Ohio: the domestic road around Lake Erie
            return [trip(points[0], TOLEDO, CLEVELAND, ERIE, points[-1])]
        return [trip(points[0], LONDON_ON, points[-1]), trip(points[0], TORONTO, points[-1])]


def test_border_helpers_find_the_foreign_stretch_and_a_point_inside_the_us():
    shape = line(DETROIT, LONDON_ON, BUFFALO)
    stretch = foreign_stretch(shape)
    assert stretch is not None and outside_us(shape[(stretch[0] + stretch[1]) // 2])
    vias = vias_inside_us(shape, stretch)
    assert all(not outside_us(v) for v in vias)
    assert vias[0][0] > vias[1][0] > vias[2][0]  # nearest the border first, then further in
    assert foreign_stretch(line(DETROIT, TOLEDO, CLEVELAND, ERIE, BUFFALO)) is None


def test_a_domestic_trip_through_canada_is_rerouted_inside_the_us(monkeypatch):
    fake = FakeValhalla()
    monkeypatch.setattr(R, "_post", fake)
    route = R.request_routes([DETROIT, BUFFALO], 40000, alternates=1)[0]
    assert all(not outside_us(p) for p in route.coords)
    assert len(fake.calls) == 2
    detroit, via, buffalo = fake.calls[1]  # the reroute went through a point inside the US
    assert (detroit, buffalo) == (DETROIT, BUFFALO) and not outside_us(via)


def test_if_the_first_via_is_in_a_lake_the_next_one_further_inside_is_tried(monkeypatch):
    fake = FakeValhalla(fail_first_via=True)
    monkeypatch.setattr(R, "_post", fake)
    route = R.request_routes([DETROIT, BUFFALO], 40000, alternates=0)[0]
    assert all(not outside_us(p) for p in route.coords)
    assert fake.calls[2][1][0] < fake.calls[1][1][0]  # second via is further south


def test_foreign_alternates_are_dropped_on_a_domestic_trip(monkeypatch):
    monkeypatch.setattr(R, "_post", FakeValhalla())
    routes = R.request_routes([DETROIT, BUFFALO], 40000, alternates=1)
    assert len(routes) == 1  # the Toronto alternate went abroad: not offered


def test_a_cross_border_trip_is_left_to_cross(monkeypatch):
    calls = []

    def valhalla(points, load_lb, alternates, avoid=()):
        calls.append(points)
        return [trip(points[0], points[-1])]

    monkeypatch.setattr(R, "_post", valhalla)
    route = R.request_routes([SEATTLE, VANCOUVER], 40000, alternates=0)[0]
    assert len(calls) == 1  # no reroute attempts
    assert outside_us(route.coords[-1])


@pytest.mark.parametrize("origin, dest, penalty", [(DETROIT, BUFFALO, 12 * 3600), (SEATTLE, VANCOUVER, 2 * 3600)])
def test_border_penalty_is_near_maximal_at_home_and_customs_abroad(monkeypatch, origin, dest, penalty):
    sent = {}

    def post_json(session, service, url, json):
        sent.update(json)
        return {"trip": trip(origin, dest)}

    monkeypatch.setattr(R, "post_json", post_json)
    R._post([origin, dest], 40000, 0)
    assert sent["costing_options"]["truck"]["country_crossing_penalty"] == penalty
    assert penalty <= 43_200  # Valhalla silently ignores anything above 12 h


def test_long_domestic_hops_get_their_extra_split_points_inside_the_us():
    marquette, bethel = (46.55, -87.44), (44.49, -70.79)  # the straight line crosses Ontario
    chunks = R._chunks([marquette, bethel])
    added = [c[-1] for c in chunks[:-1]]
    assert added and all(not outside_us(p) for p in added)


def test_split_points_of_a_cross_border_trip_are_not_moved():
    chunks = R._chunks([(32.22, -110.97), (19.43, -99.13)])  # Tucson -> Mexico City (midpoint in Durango)
    added = [c[-1] for c in chunks[:-1]]
    assert added and any(outside_us(p) for p in added)  # left in Mexico: the trip goes there

import pytest

from planner.domain.borders import in_canada, in_mexico, into_us, outside_us


@pytest.mark.parametrize(
    "place, lat, lon, outside",
    [
        ("Toronto", 43.65, -79.38, True),
        ("London, Ontario", 42.98, -81.25, True),
        ("Winnipeg", 49.90, -97.14, True),
        ("Monterrey", 25.69, -100.32, True),
        ("Detroit", 42.33, -83.05, False),
        ("Toledo", 41.65, -83.55, False),
        ("Cleveland", 41.50, -81.69, False),
        ("Erie, PA", 42.13, -80.08, False),
        ("Rochester, NY", 43.16, -77.61, False),
        ("Sault Ste. Marie, MI", 46.50, -84.35, False),
        ("Buffalo", 42.89, -78.88, False),
        ("Augusta, Maine", 44.31, -69.78, False),
        ("Fargo", 46.88, -96.79, False),
        ("El Paso", 31.76, -106.49, False),
        ("Ciudad Juarez", 31.69, -106.42, True),
        ("Seattle", 47.61, -122.33, False),
    ],
)
def test_us_or_not(place, lat, lon, outside):
    assert outside_us((lat, lon)) is outside, place


def test_points_are_moved_just_inside_the_us():
    toronto = into_us((43.65, -79.38))
    assert not outside_us(toronto) and toronto[1] == -79.38
    monterrey = into_us((25.69, -100.32))
    assert not in_mexico(monterrey)
    assert into_us((44.31, -69.78)) == (44.31, -69.78)  # already in the US: unchanged


def test_off_the_outline_is_not_canada():
    assert not in_canada((60.0, -150.0))  # Alaska is off the outline: left alone

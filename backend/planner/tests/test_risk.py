import pytest

from planner.domain.risk import Conditions, Risk, assess, rain_risk, risk_index, snow_risk, wind_risk

L, M, H, S, N = Risk.LOW, Risk.MODERATE, Risk.HIGH, Risk.SEVERE, Risk.NO_TRAVEL


@pytest.mark.parametrize(
    "mph, expected",
    [(0, L), (24.9, L), (25, M), (34.9, M), (35, H), (44.9, H), (45, S), (54.9, S), (55, N), (80, N)],
)
def test_wind_bands(mph, expected):
    assert wind_risk(mph) == expected


@pytest.mark.parametrize(
    "in_hr, expected",
    # Shared endpoints go to the riskier band; 1.00 is Severe because No Travel is "> 1.00".
    [(0, L), (0.099, L), (0.10, M), (0.249, M), (0.25, H), (0.499, H), (0.50, S), (1.00, S), (1.001, N)],
)
def test_rain_bands(in_hr, expected):
    assert rain_risk(in_hr) == expected


@pytest.mark.parametrize(
    "in_hr, expected",
    [(0, L), (0.49, L), (0.5, M), (0.99, M), (1.0, H), (1.99, H), (2.0, S), (3.0, S), (3.01, N)],
)
def test_snow_bands(in_hr, expected):
    assert snow_risk(in_hr) == expected


def calm(**kw):
    return Conditions(**{"wind_mph": 5, "rain_in_hr": 0, "snow_in_hr": 0, **kw})


@pytest.mark.parametrize(
    "mph, load, expected",
    [
        (55, 0, N),  # >= 55 mph is No Travel for any load
        (50, 30_000, S),  # 45-54 mph needs MORE than 30,000 lb to become No Travel
        (50, 30_001, N),
        (40, 40_000, H),  # 35-44 mph needs MORE than 40,000 lb to become Severe
        (40, 40_001, S),
        (40, 35_000, H),  # heavy, but under the 35-44 mph threshold
        (30, 79_000, M),  # load rules never touch wind under 35 mph
    ],
)
def test_load_rules(mph, load, expected):
    assert assess(calm(wind_mph=mph), load).level == expected


def test_overall_is_worst_factor_with_reasons():
    a = assess(calm(wind_mph=30, rain_in_hr=0.6, snow_in_hr=0.2), load_lb=10_000)
    assert (a.level, a.wind, a.rain, a.snow) == (S, M, S, L)
    assert a.reasons == ("rain 0.60 in/hr",)


def test_load_rule_is_named_in_reasons():
    a = assess(calm(wind_mph=48), load_lb=45_000)
    assert a.level == N
    assert "load over 30,000 lb" in a.reasons[0]


def test_low_risk_has_no_reasons():
    assert assess(calm(), 20_000).reasons == ()


def test_risk_index_is_continuous_and_never_below_level():
    assert risk_index(calm(wind_mph=0), 0) == 0
    assert risk_index(calm(wind_mph=30), 0) == pytest.approx(1.5)
    assert risk_index(calm(wind_mph=40), 45_000) >= float(S)  # load rule lifts the index
    assert risk_index(calm(wind_mph=90), 0) == 4

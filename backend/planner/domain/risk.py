"""Weather risk classification from the assessment's risk and load rules.

Levels are ordered integers so they can be compared, averaged and sorted:
0 Low, 1 Moderate, 2 High, 3 Severe, 4 No Travel.

Boundary convention (the table's ranges share endpoints, e.g. rain 0.25 is both
"0.10-0.25" and "0.25-0.50"): a value that sits exactly on a shared endpoint
goes to the riskier level, except where the table says ">" for No Travel
(rain 1.00 and snow 3.00 are Severe, anything above is No Travel).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Risk(IntEnum):
    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()

    @property
    def key(self) -> str:
        return self.name.lower()


# Lower bound of Moderate, High, Severe for each condition.
WIND_MPH = (25.0, 35.0, 45.0)
RAIN_IN_HR = (0.10, 0.25, 0.50)
SNOW_IN_HR = (0.5, 1.0, 2.0)

WIND_NO_TRAVEL_MPH = 55.0  # >= is No Travel
RAIN_NO_TRAVEL_IN_HR = 1.00  # > is No Travel
SNOW_NO_TRAVEL_IN_HR = 3.0  # > is No Travel

# Load rules.
HEAVY_LOAD_NO_TRAVEL_LB = 30_000  # 45-54 mph and load above this -> No Travel
HEAVY_LOAD_SEVERE_LB = 40_000  # 35-44 mph and load above this -> Severe


@dataclass(frozen=True)
class Conditions:
    wind_mph: float
    rain_in_hr: float
    snow_in_hr: float
    gust_mph: float | None = None  # shown to the driver, not used by the rules


@dataclass(frozen=True)
class Assessment:
    level: Risk
    wind: Risk
    rain: Risk
    snow: Risk
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _banded(value: float, bounds: tuple[float, float, float]) -> Risk:
    moderate, high, severe = bounds
    if value >= severe:
        return Risk.SEVERE
    if value >= high:
        return Risk.HIGH
    if value >= moderate:
        return Risk.MODERATE
    return Risk.LOW


def wind_risk(mph: float) -> Risk:
    if mph >= WIND_NO_TRAVEL_MPH:
        return Risk.NO_TRAVEL
    return _banded(mph, WIND_MPH)


def rain_risk(in_hr: float) -> Risk:
    if in_hr > RAIN_NO_TRAVEL_IN_HR:
        return Risk.NO_TRAVEL
    return _banded(in_hr, RAIN_IN_HR)


def snow_risk(in_hr: float) -> Risk:
    if in_hr > SNOW_NO_TRAVEL_IN_HR:
        return Risk.NO_TRAVEL
    return _banded(in_hr, SNOW_IN_HR)


def wind_risk_for_load(mph: float, load_lb: float) -> tuple[Risk, str | None]:
    """Wind level after the load rules, plus the rule that fired (if any)."""
    base = wind_risk(mph)
    if base == Risk.SEVERE and load_lb > HEAVY_LOAD_NO_TRAVEL_LB:
        return Risk.NO_TRAVEL, f"45-54 mph wind with a load over {HEAVY_LOAD_NO_TRAVEL_LB:,} lb"
    if base == Risk.HIGH and load_lb > HEAVY_LOAD_SEVERE_LB:
        return Risk.SEVERE, f"35-44 mph wind with a load over {HEAVY_LOAD_SEVERE_LB:,} lb"
    return base, None


def assess(conditions: Conditions, load_lb: float) -> Assessment:
    """Overall level = the worst of wind (load-adjusted), rain and snow."""
    wind, load_rule = wind_risk_for_load(conditions.wind_mph, load_lb)
    rain = rain_risk(conditions.rain_in_hr)
    snow = snow_risk(conditions.snow_in_hr)
    level = max(wind, rain, snow)

    reasons: list[str] = []
    if level > Risk.LOW:
        if wind == level:
            reasons.append(load_rule or f"wind {conditions.wind_mph:.0f} mph")
        if rain == level:
            reasons.append(f"rain {conditions.rain_in_hr:.2f} in/hr")
        if snow == level:
            reasons.append(f"snow {conditions.snow_in_hr:.2f} in/hr")
    return Assessment(level=level, wind=wind, rain=rain, snow=snow, reasons=tuple(reasons))


# Continuous 0-4 index for the heatmap: linear between the table's thresholds,
# so the colour ramp lines up with the discrete levels.
_WIND_STOPS = (0.0, *WIND_MPH, WIND_NO_TRAVEL_MPH)
_RAIN_STOPS = (0.0, *RAIN_IN_HR, RAIN_NO_TRAVEL_IN_HR)
_SNOW_STOPS = (0.0, *SNOW_IN_HR, SNOW_NO_TRAVEL_IN_HR)


def _index(value: float, stops: tuple[float, ...]) -> float:
    if value <= stops[0]:
        return 0.0
    for i in range(1, len(stops)):
        if value < stops[i]:
            lo, hi = stops[i - 1], stops[i]
            return (i - 1) + (value - lo) / (hi - lo)
    return 4.0


def risk_index(conditions: Conditions, load_lb: float) -> float:
    """Smooth risk score in [0, 4] for visualisation; never below the discrete level."""
    raw = max(
        _index(conditions.wind_mph, _WIND_STOPS),
        _index(conditions.rain_in_hr, _RAIN_STOPS),
        _index(conditions.snow_in_hr, _SNOW_STOPS),
    )
    return max(raw, float(assess(conditions, load_lb).level))

"""Route scoring and the recommendation: weather risk balanced against travel time.

The brief asks to recommend the best route "balancing weather risk and travel
time", preferring fewer Severe miles, then fewer High miles, then lower average
risk, then shorter travel time. We turn that into one number, in hours:

    score = travel hours + weather minutes / 60

where each mile's weather minutes come from what it costs a carrier:

- Bad weather multiplies the crash rate. Each hazard (load-adjusted wind, rain,
  snow) has a multiplier by level, and hazards on the same mile multiply each
  other: crash models treat weather factors as multipliers, and a wet road also
  lowers the wind it takes to push a truck sideways.
- The extra crash risk is priced at the full cost of a crash to society (injuries,
  lives, property, mostly in the other vehicles), not just the carrier's insurance
  bill, converted to minutes of truck time, then made RISK_AVERSION times more
  cautious: the average hides the rare crash that kills someone, and the carrier's
  own worst case (an award above its policy limit) can end the company.

One hazard alone works out at about 0.8 min per Moderate mile, 2.2 per High mile
and 10 per Severe mile, so 30 extra minutes buys avoiding 3 Severe miles. Severe
wind in High rain is 30x the crash rate, about 32 min per mile.

Two rules sit above the score, both cautious:
- No Travel miles always lose: a route you're told not to drive is never better.
- Miles with no forecast come next: missing data never makes a route look safer.
Each mile counts once, in its own level.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from .risk import Risk

# The business inputs (sources in the README).
TRUCK_COST_PER_MIN = 1.50  # USD; ATRI operating cost, about $91 per hour
CRASH_COST_PER_MI = 0.24  # USD; ~$150k full cost of an average truck crash x ~1.6 crashes per million truck miles
RISK_AVERSION = 7  # how much more than the average crash cost we pay to avoid one

# How many times the normal crash rate a mile in each hazard level carries. Moderate:
# rain/snow studies (+70-85%); Severe: single-truck crashes in snow (~9.7x).
CRASH_MULTIPLIER = {Risk.LOW: 1.0, Risk.MODERATE: 1.7, Risk.HIGH: 3.0, Risk.SEVERE: 10.0}

# Minutes of driving worth one "normal mile" of extra crash risk: about 1.1.
MINUTES_PER_EXTRA_RISK = CRASH_COST_PER_MI / TRUCK_COST_PER_MIN * RISK_AVERSION


def weather_minutes(hazards: Iterable[Risk]) -> float:
    """Minutes of extra driving one mile with these hazard levels is worth avoiding.

    No Travel is handled by its own rule above the score, so it counts as Severe here.
    """
    multiplier = math.prod(CRASH_MULTIPLIER[min(h, Risk.SEVERE)] for h in hazards)
    return MINUTES_PER_EXTRA_RISK * (multiplier - 1)

# Miles are compared to the nearest whole mile and the score to the minute, so
# rounding noise (0.04 mi) never decides between two routes.
MILES_PRECISION = 0


@dataclass(frozen=True)
class RouteScore:
    route_id: str
    miles_by_level: dict[Risk, float]
    avg_risk: float  # distance-weighted mean level over the miles with a forecast, 0-4
    duration_s: float  # time until arrival (including any wait before leaving)
    unknown_mi: float = 0.0  # miles with no forecast
    risk_min: float = 0.0  # weather risk as minutes of extra driving it's worth avoiding

    @property
    def no_travel_mi(self) -> float:
        return self.miles_by_level.get(Risk.NO_TRAVEL, 0.0)

    @property
    def severe_mi(self) -> float:
        return self.miles_by_level.get(Risk.SEVERE, 0.0)

    @property
    def high_mi(self) -> float:
        return self.miles_by_level.get(Risk.HIGH, 0.0)

    @property
    def score_h(self) -> float:
        """Travel time plus the weather risk, in hours: lower is better."""
        return (self.duration_s / 60 + self.risk_min) / 60


def score_route(route_id: str, legs: Iterable[tuple], duration_s: float) -> RouteScore:
    """`legs` are (level, miles) or (level, miles, hazard levels), one per checkpoint.

    Level None = no forecast. Without hazard levels, the level is the only hazard.
    """
    miles_by_level = {level: 0.0 for level in Risk}
    total = weighted = unknown = risk_min = 0.0
    for level, miles, *hazards in legs:
        if level is None:
            unknown += miles
            continue
        miles_by_level[level] += miles
        total += miles
        weighted += int(level) * miles
        if level != Risk.NO_TRAVEL:
            risk_min += miles * weather_minutes(hazards[0] if hazards else (level,))
    avg = weighted / total if total else 0.0
    return RouteScore(route_id, miles_by_level, avg, duration_s, unknown, risk_min)


def sort_key(score: RouteScore) -> tuple[float, ...]:
    """No Travel miles, then miles without a forecast, then the score (to the minute)."""
    return (
        round(score.no_travel_mi, MILES_PRECISION),
        round(score.unknown_mi, MILES_PRECISION),
        round(score.score_h * 60),
    )


def risk_key(score: RouteScore) -> tuple[float, ...]:
    """The same, without travel time: is one option *safer* than another?"""
    return (round(score.no_travel_mi, MILES_PRECISION), round(score.unknown_mi, MILES_PRECISION), round(score.risk_min))


def rank(scores: list[RouteScore]) -> list[RouteScore]:
    """Best first. Stable, so fully tied routes keep the routing API's order."""
    return sorted(scores, key=sort_key)


def _risk_words(s: RouteScore) -> str:
    parts = [
        f"{round(m)} {lvl.label} mi"
        for lvl in (Risk.SEVERE, Risk.HIGH, Risk.MODERATE)
        if (m := s.miles_by_level.get(lvl, 0.0)) >= 0.5
    ]
    return ", ".join(parts) or "only Low miles"


def explain(best: RouteScore, runner_up: RouteScore | None) -> str:
    """One sentence on why the best route beat the next one."""
    if runner_up is None:
        return "Only one route was available."
    if round(best.no_travel_mi) < round(runner_up.no_travel_mi):
        return f"Avoids No Travel conditions: {best.no_travel_mi:.0f} mi vs {runner_up.no_travel_mi:.0f} mi on the next option."
    if round(best.unknown_mi) < round(runner_up.unknown_mi):
        return f"Fewer miles without a forecast: {best.unknown_mi:.0f} vs {runner_up.unknown_mi:.0f} on the next option."
    slower_min = round((best.duration_s - runner_up.duration_s) / 60)
    safer_min = round(runner_up.risk_min - best.risk_min)
    if slower_min > 0:
        return (
            f"{slower_min} min slower than the next option, but avoids weather worth {safer_min} min "
            f"({_risk_words(best)} vs {_risk_words(runner_up)})."
        )
    if safer_min > 0:
        lead = f"{-slower_min} min faster and less" if slower_min < 0 else "Less"
        return f"{lead} weather risk than the next option ({_risk_words(best)} vs {_risk_words(runner_up)})."
    if slower_min < 0:
        return f"Same weather exposure as the next option, and {-slower_min} min faster."
    return "Ties with the next option; kept the routing API's first choice."

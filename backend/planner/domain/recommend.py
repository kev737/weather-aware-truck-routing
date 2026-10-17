"""Route scoring and the recommendation: weather risk balanced against travel time.

The brief asks to recommend the best route "balancing weather risk and travel
time", preferring fewer Severe miles, then fewer High miles, then lower average
risk, then shorter travel time. We turn that into one number, in hours:

    score = travel hours + (10 min x Severe mi + 2 min x High mi + 0.5 min x Moderate mi) / 60

so a route can be worth a detour: 30 extra minutes buys avoiding 3 Severe miles.
The weights keep the brief's order (a Severe mile costs 5x a High mile, which
costs 4x a Moderate one) and are business judgment, tuned here as "balanced".

Two rules sit above the score, both cautious:
- No Travel miles always lose: a route you're told not to drive is never better.
- Miles with no forecast come next: missing data never makes a route look safer.
Each mile counts once, in its own level.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .risk import Risk

# Extra minutes of driving one mile at each level is "worth".
RISK_MINUTES_PER_MILE = {Risk.MODERATE: 0.5, Risk.HIGH: 2.0, Risk.SEVERE: 10.0}

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
    def risk_min(self) -> float:
        """Weather risk as minutes of extra driving it's worth avoiding."""
        return sum(self.miles_by_level.get(lvl, 0.0) * w for lvl, w in RISK_MINUTES_PER_MILE.items())

    @property
    def score_h(self) -> float:
        """Travel time plus the weather risk, in hours: lower is better."""
        return (self.duration_s / 60 + self.risk_min) / 60


def score_route(route_id: str, legs: Iterable[tuple[Risk | None, float]], duration_s: float) -> RouteScore:
    """`legs` are (risk level, miles) pairs, one per checkpoint; level None = no forecast."""
    miles_by_level = {level: 0.0 for level in Risk}
    total = weighted = unknown = 0.0
    for level, miles in legs:
        if level is None:
            unknown += miles
            continue
        miles_by_level[level] += miles
        total += miles
        weighted += int(level) * miles
    avg = weighted / total if total else 0.0
    return RouteScore(route_id, miles_by_level, avg, duration_s, unknown)


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

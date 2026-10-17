"""Route scoring and the recommendation order from the brief.

Prefer, in order: fewest Severe miles, fewest High miles, lowest average risk,
shortest travel time. Two additions, both cautious:
- No Travel miles are ranked ahead of everything (a route you're told not to
  drive is never better than one that's only Severe), and they also count toward
  Severe miles, since No Travel is at least Severe.
- Miles with no forecast come next: missing data never makes a route look safer.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .risk import Risk

# Miles are compared to the nearest whole mile, average risk to 2 decimals and time
# to the minute, so rounding noise (0.04 mi) never beats a route that is 40 minutes faster.
MILES_PRECISION = 0
AVG_RISK_PRECISION = 2


@dataclass(frozen=True)
class RouteScore:
    route_id: str
    miles_by_level: dict[Risk, float]
    avg_risk: float  # distance-weighted mean level over the miles with a forecast, 0-4
    duration_s: float
    unknown_mi: float = 0.0  # miles with no forecast

    @property
    def no_travel_mi(self) -> float:
        return self.miles_by_level.get(Risk.NO_TRAVEL, 0.0)

    @property
    def severe_mi(self) -> float:
        """Severe or worse."""
        return self.miles_by_level.get(Risk.SEVERE, 0.0) + self.no_travel_mi

    @property
    def high_mi(self) -> float:
        return self.miles_by_level.get(Risk.HIGH, 0.0)


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


# (label, getter, decimals, unit) in priority order.
CRITERIA = (
    ("No Travel miles", lambda s: s.no_travel_mi, MILES_PRECISION, "mi"),
    ("miles without a forecast", lambda s: s.unknown_mi, MILES_PRECISION, "mi"),
    ("Severe miles", lambda s: s.severe_mi, MILES_PRECISION, "mi"),
    ("High miles", lambda s: s.high_mi, MILES_PRECISION, "mi"),
    ("average risk", lambda s: s.avg_risk, AVG_RISK_PRECISION, ""),
    ("travel time", lambda s: s.duration_s / 60, 0, "min"),
)


def sort_key(score: RouteScore) -> tuple[float, ...]:
    return tuple(round(get(score), decimals) for _, get, decimals, _ in CRITERIA)


def rank(scores: list[RouteScore]) -> list[RouteScore]:
    """Best first. Stable, so fully tied routes keep the routing API's order."""
    return sorted(scores, key=sort_key)


def explain(best: RouteScore, runner_up: RouteScore | None) -> str:
    """One sentence on the criterion that decided between the top two routes."""
    if runner_up is None:
        return "Only one route was available."
    for label, get, decimals, unit in CRITERIA:
        a, b = round(get(best), decimals), round(get(runner_up), decimals)
        if a != b:
            if unit == "min":
                return f"Same weather exposure as the next option, and {b - a:.0f} min faster."
            if unit == "mi":
                return f"Fewest {label}: {a:.0f} mi vs {b:.0f} mi on the next option."
            return f"Lowest {label}: {a:.2f} vs {b:.2f} on the next option."
    return "Ties with the next option on every criterion; kept the routing API's first choice."

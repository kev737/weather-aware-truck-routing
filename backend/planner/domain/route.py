"""Route geometry with timing, and checkpoint sampling along it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .geo import LatLon, cumulative_miles, interpolate, point_at


@dataclass
class Route:
    coords: list[LatLon]
    cum_mi: list[float]  # miles from the start at each vertex
    cum_s: list[float]  # driving seconds from the start at each vertex
    via: str = ""  # main roads, for a human-readable name
    roads: dict[str, float] = field(default_factory=dict)  # road name -> miles driven on it

    @property
    def distance_mi(self) -> float:
        return self.cum_mi[-1]

    @property
    def duration_s(self) -> float:
        return self.cum_s[-1]

    def seconds_at(self, mile: float) -> float:
        return interpolate(self.cum_mi, self.cum_s, mile)

    def without(self, i: int, j: int) -> "Route":
        """The route with vertices i+1 .. j-1 cut out (i and j are joined directly)."""
        coords = self.coords[: i + 1] + self.coords[j:]
        skipped_s = self.cum_s[j] - self.cum_s[i]
        cum_s = self.cum_s[: i + 1] + [t - skipped_s for t in self.cum_s[j:]]
        return Route(coords=coords, cum_mi=cumulative_miles(coords), cum_s=cum_s, via=self.via, roads=self.roads)

    @classmethod
    def from_timed_segments(
        cls,
        coords: list[LatLon],
        segments: list[tuple[int, int, float]],
        via: str = "",
        roads: dict[str, float] | None = None,
    ) -> "Route":
        """Build a route from shape-index spans with a duration each.

        `segments` is (begin_index, end_index, seconds), as routing APIs return per
        maneuver/step. Each span's time is spread over its vertices by distance, so
        speed can vary along the route (highway vs city streets).
        """
        cum_mi = cumulative_miles(coords)
        cum_s = [0.0] * len(coords)
        elapsed = 0.0
        last = 0
        for begin, end, seconds in segments:
            begin, end = max(begin, last), min(end, len(coords) - 1)
            span = cum_mi[end] - cum_mi[begin]
            for i in range(begin, end + 1):
                frac = 1.0 if span == 0 else (cum_mi[i] - cum_mi[begin]) / span
                cum_s[i] = elapsed + frac * seconds
            elapsed += seconds
            last = end
        for i in range(last + 1, len(coords)):
            cum_s[i] = elapsed
        return cls(coords=coords, cum_mi=cum_mi, cum_s=cum_s, via=via, roads=roads or {})


@dataclass(frozen=True)
class Sample:
    mile: float
    point: LatLon
    seconds: float  # driving time from departure to this checkpoint
    start_mi: float  # the stretch of road this checkpoint stands for
    end_mi: float

    @property
    def covers_mi(self) -> float:
        return self.end_mi - self.start_mi


def sample_checkpoints(route: Route, interval_mi: float) -> list[Sample]:
    """Checkpoints at the start, every `interval_mi`, and at the destination.

    Each checkpoint stands for the stretch of road halfway to its neighbours, so the
    covered miles add up to the route length and risk miles can be summed directly.
    """
    total = route.distance_mi
    miles = [0.0]
    m = interval_mi
    while m < total - 0.5:  # skip a sliver checkpoint right before the destination
        miles.append(m)
        m += interval_mi
    if total > 0:
        miles.append(total)

    samples = []
    for k, mile in enumerate(miles):
        start = 0.0 if k == 0 else (miles[k - 1] + mile) / 2
        end = total if k == len(miles) - 1 else (mile + miles[k + 1]) / 2
        samples.append(
            Sample(
                mile=mile,
                point=point_at(route.coords, route.cum_mi, mile),
                seconds=route.seconds_at(mile),
                start_mi=start,
                end_mi=end,
            )
        )
    return samples


# "I 35", "US 290", "TX 130": the names drivers and signs use. Local names ("Pan Am
# Expressway" is I 35 in San Antonio) only name a route when it has no numbered road.
NUMBERED_ROAD = re.compile(r"^(I|US|SR|[A-Z]{2})[ -]?\d")


def distinguishing_names(routes: list[Route], n: int = 2) -> list[str]:
    """A name per route from the roads it uses much more than the other routes.

    "I 80 / I 70" for two routes that both mostly use I-80 says nothing; naming each
    by what sets it apart ("via US 30", "via I 40") lets a driver tell them apart.
    """
    names = []
    for i, route in enumerate(routes):
        others = [r for j, r in enumerate(routes) if j != i]

        def lead(road: str) -> float:
            return route.roads[road] - max((o.roads.get(road, 0.0) for o in others), default=0.0)

        distinct = [road for road in sorted(route.roads, key=lead, reverse=True) if lead(road) > 20]
        numbered = [road for road in distinct if NUMBERED_ROAD.match(road)]
        distinct = (numbered or distinct)[:n]
        names.append(" / ".join(distinct) or route.via)
    return names

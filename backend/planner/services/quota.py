"""Our own count of Open-Meteo usage, so we can warn before the free tier runs out.

Open-Meteo's free tier allows 600 calls a minute, 5,000 an hour and under 10,000 a
day per IP address; each forecast location in a request counts as one call (more
only past 10 variables or 2 weeks of data, which we never request). We count what
we download in the shared cache, per UTC minute, hour and day, and refuse a
download that would cross the daily line: retrying can't help until tomorrow.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.cache import cache

from .http import RateLimited

WARN_AT = 0.8  # tell the user once today's usage passes 80%


class QuotaExhausted(RateLimited):
    """A limit retrying soon won't fix (the daily one)."""

    def __init__(self, message: str):
        super().__init__("weather", message)


@dataclass(frozen=True)
class Window:
    name: str
    limit: int
    fmt: str  # strftime pattern that names the current window
    seconds: int


def _windows() -> tuple[Window, ...]:
    return (
        Window("minute", settings.OPEN_METEO_LIMITS["minute"], "%Y%m%d%H%M", 120),
        Window("hour", settings.OPEN_METEO_LIMITS["hour"], "%Y%m%d%H", 2 * 3600),
        Window("day", settings.OPEN_METEO_LIMITS["day"], "%Y%m%d", 2 * 86400),
    )


def _key(w: Window, now: datetime) -> str:
    return f"quota:{w.name}:{now:{w.fmt}}"


RECENT_KEY = "quota:recent"  # [(epoch seconds, calls), ...] for the last minute


def _recent(now: datetime) -> list[tuple[float, int]]:
    cutoff = now.timestamp() - 60
    return [(t, n) for t, n in cache.get(RECENT_KEY, []) if t > cutoff]


def record(calls: int, now: datetime | None = None) -> None:
    """Add `calls` to every window's counter, and to the last-minute log."""
    now = now or datetime.now(timezone.utc)
    cache.set(RECENT_KEY, _recent(now) + [(now.timestamp(), calls)], 120)
    for w in _windows():
        key = _key(w, now)
        cache.add(key, 0, w.seconds)
        try:
            cache.incr(key, calls)
        except ValueError:  # expired between add and incr
            cache.set(key, calls, w.seconds)


def used(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    return {w.name: cache.get(_key(w, now), 0) for w in _windows()}


MINUTE_MARGIN = 0.95  # stay a little under the per-minute limit


def minute_budget(now: datetime | None = None) -> int:
    """Calls we can still make now: the per-minute limit minus what we fetched in the
    last 60 seconds (Open-Meteo's minute is rolling, not on the clock), and never more
    than the hour or the day has left."""
    now = now or datetime.now(timezone.utc)
    left = [per_minute() - sum(n for _, n in _recent(now))]
    for w in _windows():
        if w.name != "minute":
            left.append(w.limit - cache.get(_key(w, now), 0))  # the hour and the day
    return max(0, min(left))


def minute_limit_text() -> str:
    """The provider's per-minute limit, as the messages state it ("600 lookups a minute")."""
    return f"{settings.OPEN_METEO_LIMITS['minute']:,} lookups a minute"


def per_minute() -> int:
    """What we let ourselves fetch in any 60 seconds."""
    return int(settings.OPEN_METEO_LIMITS["minute"] * MINUTE_MARGIN)


def seconds_until_free(now: datetime | None = None) -> int:
    """When the oldest fetch of the last minute leaves the window (and its budget comes back)."""
    now = now or datetime.now(timezone.utc)
    recent = _recent(now)
    if not recent:
        return 1
    return max(1, int(recent[0][0] + 61 - now.timestamp()))


def check_daily(calls: int, now: datetime | None = None) -> None:
    """Refuse a download that would cross today's limit."""
    now = now or datetime.now(timezone.utc)
    day = next(w for w in _windows() if w.name == "day")
    if used(now)["day"] + calls > day.limit:
        raise QuotaExhausted(daily_message())


def daily_message() -> str:
    limit = settings.OPEN_METEO_LIMITS["day"]
    return f"today's free weather quota ({limit:,} lookups) is used up. Please try again tomorrow."


def status(now: datetime | None = None) -> dict:
    """Usage per window, for the API response and /api/usage/."""
    now = now or datetime.now(timezone.utc)
    counts = used(now)
    out = {}
    for w in _windows():
        out[w.name] = {"used": counts[w.name], "limit": w.limit, "pct": round(100 * counts[w.name] / w.limit)}
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    out["day"]["resets"] = tomorrow.isoformat()
    out["near_daily_limit"] = counts["day"] >= WARN_AT * settings.OPEN_METEO_LIMITS["day"]
    return out


def from_provider_error(reason: str) -> RateLimited:
    """Open-Meteo's 429 says which limit was hit; only the per-minute one clears quickly."""
    lowered = reason.lower()
    if "daily" in lowered:
        return QuotaExhausted(daily_message())
    if "hourly" in lowered:
        return QuotaExhausted("the free weather service's hourly limit was reached. Please try again in an hour.")
    return RateLimited("weather", "the free tier's per-minute limit was reached, retrying shortly")

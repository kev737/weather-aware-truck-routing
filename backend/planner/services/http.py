"""Shared HTTP session and the error type for third-party API failures."""
from __future__ import annotations

import requests
from django.conf import settings


class UpstreamError(Exception):
    """A third-party API (routing, geocoding, weather) failed or returned nothing usable."""

    def __init__(self, service: str, message: str):
        super().__init__(f"{service}: {message}")
        self.service = service
        self.message = message


class RateLimited(UpstreamError):
    """The provider asked us to slow down (HTTP 429), or our own pacing did."""

    def __init__(self, service: str, message: str, retry_after_s: int | None = None):
        super().__init__(service, message)
        self.retry_after_s = retry_after_s


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = settings.HTTP_USER_AGENT
    return s


def get_json(s: requests.Session, service: str, url: str, **kwargs):
    return _request(s, service, "GET", url, **kwargs)


def post_json(s: requests.Session, service: str, url: str, **kwargs):
    return _request(s, service, "POST", url, **kwargs)


def _request(s: requests.Session, service: str, method: str, url: str, **kwargs):
    kwargs.setdefault("timeout", settings.HTTP_TIMEOUT_S)
    try:
        resp = s.request(method, url, **kwargs)
    except requests.RequestException as exc:
        raise UpstreamError(service, f"request failed ({exc.__class__.__name__})") from exc
    if resp.status_code == 429:
        try:
            reason = _error_detail(resp.json())  # e.g. Open-Meteo says which limit was hit
        except ValueError:
            reason = None
        raise RateLimited(service, reason or "the free tier's rate limit was reached, try again in about a minute")
    try:
        data = resp.json()
    except ValueError as exc:
        raise UpstreamError(service, f"bad response (HTTP {resp.status_code})") from exc
    if resp.status_code >= 400:
        raise UpstreamError(service, _error_detail(data) or f"HTTP {resp.status_code}")
    return data


def _error_detail(data) -> str | None:
    """The human-readable message from the error shapes our providers use."""
    if not isinstance(data, dict):
        return None
    err = data.get("error") or data.get("detailedError") or data.get("reason")
    if isinstance(err, dict):
        err = err.get("description") or err.get("message")
    return str(err) if err else None

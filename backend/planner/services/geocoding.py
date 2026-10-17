"""Place search with Photon (OpenStreetMap data, no API key).

Photon's own ranking is weak on partial input ("San Anto" puts a district in Peru
first and San Antonio, TX fourth), so we ask for more candidates than we show and
re-rank them for a truck dispatcher: towns and cities first, North America first,
names that start with what was typed first. Street addresses keep their place when
the query looks like one (starts with a house number).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from .http import get_json, session

_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

CANDIDATES = 15
# Places, roads and addresses; leaves out the shops, farms and landmarks that crowd results.
LAYERS = ("city", "locality", "district", "county", "street", "house")
TYPE_SCORE = {"city": 3.0, "town": 3.0, "village": 2.5, "locality": 2.0, "district": 1.5, "county": 1.0}
COUNTRY_SCORE = {"US": 2.0, "CA": 1.0, "MX": 1.0}


class PlaceNotFound(ValueError):
    pass


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float

    def as_dict(self) -> dict:
        return {"name": self.name, "lat": self.lat, "lon": self.lon}


def _label(props: dict) -> str:
    name = props.get("name")
    if not name and props.get("street"):  # bare addresses have no name
        name = " ".join(p for p in (props.get("housenumber"), props.get("street")) if p)
    if props.get("type") == "county" and name and "county" not in name.lower():
        name += " County"  # otherwise "Dallas, Texas, US" shows up twice (city and county)
    city = props.get("city")
    # The state stays even when it repeats the name: "New York, New York, US".
    parts = [name, city if city != name else None, props.get("state"), props.get("countrycode")]
    return ", ".join(p for p in parts if p)


def _score(props: dict, query: str, rank: int) -> float:
    words = query.lower().replace(",", " ").split()
    name = (props.get("name") or "").lower()
    looks_like_address = query[:1].isdigit()
    score = 0.0 if looks_like_address else TYPE_SCORE.get(props.get("type", ""), 0.0)
    score += COUNTRY_SCORE.get(props.get("countrycode", ""), 0.0)
    if words and name.startswith(words[0]):
        score += 1.5
    return score - rank * 0.1  # Photon's order breaks ties


def search(query: str, limit: int = 5) -> list[Place]:
    """Places matching `query`, best first. Accepts "lat, lon" as well."""
    query = query.strip()
    if m := _LATLON.match(query):
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return [Place(f"{lat:.4f}, {lon:.4f}", lat, lon)]
    if len(query) < 2:
        return []

    key = "geocode:" + hashlib.sha1(query.lower().encode()).hexdigest()
    if (ranked := cache.get(key)) is None:
        data = get_json(
            session(),
            "geocoding",
            settings.PHOTON_URL,
            # Bias toward the continental US; Spotter's drivers are US carriers.
            params={
                "q": query,
                "limit": CANDIDATES,
                "lang": "en",
                "lat": 39.5,
                "lon": -98.35,
                "zoom": 4,  # bias toward a whole-country view around that point
                "location_bias_scale": 0.5,
                "layer": LAYERS,
            },
        )
        features = data.get("features", [])
        order = sorted(range(len(features)), key=lambda i: -_score(features[i]["properties"], query, i))
        ranked, labels = [], set()
        for i in order:
            f = features[i]
            label = _label(f["properties"])
            if label and label not in labels:
                labels.add(label)
                ranked.append(Place(label, f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]))
        cache.set(key, ranked, settings.GEOCODE_CACHE_S)
    return ranked[:limit]


def resolve(value: str | dict) -> Place:
    """A form value (free text, or {name, lat, lon} picked from suggestions) to a Place."""
    if isinstance(value, dict):
        return Place(value.get("name") or f"{value['lat']:.4f}, {value['lon']:.4f}", value["lat"], value["lon"])
    places = search(value, limit=1)
    if not places:
        raise PlaceNotFound(f'No place found for "{value}".')
    return places[0]

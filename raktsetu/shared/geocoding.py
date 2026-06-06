"""Geocoding via OpenStreetMap Nominatim.

Used to convert a free-text area/hospital name into lat/lng for proximity
matching. Nominatim's usage policy requires a descriptive User-Agent and a
max of 1 request/second; callers should not geocode in tight loops.
"""
from __future__ import annotations

import logging
import math
from typing import Optional, Tuple

from . import config

logger = logging.getLogger("raktsetu.geocoding")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def geocode(area: str, city: Optional[str] = None) -> Optional[Tuple[float, float]]:
    """Return (lat, lng) for a place, or None if not found / on error."""
    if not area:
        return None
    if config.LOCAL_MODE:
        # Avoid network in local test harness; return a deterministic-ish stub
        # centred on Hyderabad so distance math still works.
        return _stub_coords(area)
    city = city or config.get("DEFAULT_CITY", "Hyderabad")
    query = f"{area} {city}".strip()
    try:
        import requests

        resp = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": config.get("NOMINATIM_USER_AGENT", "raktsetu-bloodwarriors")},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as exc:
        logger.warning("Geocoding failed for %r: %s", query, exc)
        return None


def _stub_coords(area: str) -> Tuple[float, float]:
    # Hyderabad centre + small deterministic offset from the area string hash.
    base_lat, base_lng = 17.3850, 78.4867
    h = sum(ord(c) for c in area)
    return base_lat + (h % 50) / 1000.0, base_lng + (h % 37) / 1000.0


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Great-circle distance in km between two (lat, lng) points."""
    (lat1, lon1), (lat2, lon2) = a, b
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))

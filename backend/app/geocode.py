"""
Store address → coordinates, for the pantry map (plan Phase 2).

Runs once per store, when its address is set or changed, never on a page
load. The result is stored on the Store row, so the map reads plain numbers.

Uses OpenStreetMap's Nominatim. Its usage policy asks for an identifying
User-Agent and no more than about one request a second. At store-signup
volume that is far below the limit, but do not call this in a loop.

Off by default in tests and anywhere the lookup should not leave the
machine. A failed or disabled lookup returns None, and the store is simply
left off the map until its address is fixed. Nothing else is blocked on it.
"""
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

GEOCODE_ENABLED = os.getenv("GEOCODE_ENABLED", "false").lower() in ("1", "true", "yes")
GEOCODE_URL = os.getenv("GEOCODE_URL", "https://nominatim.openstreetmap.org/search")
GEOCODE_TIMEOUT = float(os.getenv("GEOCODE_TIMEOUT_SECONDS", "5"))


def _lookup(params: dict) -> Optional[tuple[float, float]]:
    """One Nominatim search. None on any failure, so callers degrade the same
    way whether the service is off, down, slow or has never heard of it."""
    query = urllib.parse.urlencode({**params, "format": "json", "limit": 1})
    request = urllib.request.Request(
        f"{GEOCODE_URL}?{query}",
        headers={"User-Agent": "NourishNet/0.3 (store setup; contact via project repo)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=GEOCODE_TIMEOUT) as response:
            results = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    if not results:
        return None
    try:
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def geocode_address(address: Optional[str]) -> Optional[tuple[float, float]]:
    """(latitude, longitude) for a free-text address, or None."""
    if not GEOCODE_ENABLED or not address or not address.strip():
        return None
    return _lookup({"q": address.strip()})


_ZIP_PATTERN = re.compile(r"^\d{5}$")

# Zip codes do not move, and a pantry typing the same one repeatedly should not
# cost a request each time. Successes only: a timeout is not worth remembering.
_zip_cache: dict[str, tuple[float, float]] = {}


def normalize_zip(value: Optional[str]) -> Optional[str]:
    """The 5-digit US zip in `value`, or None. Accepts ZIP+4 and stray spaces."""
    if not value:
        return None
    five = value.strip().split("-")[0].strip()
    return five if _ZIP_PATTERN.match(five) else None


def geocode_zip(zip_code: str) -> Optional[tuple[float, float]]:
    """(latitude, longitude) of the middle of a US zip code, or None.

    Expects a value already passed through normalize_zip. Uses the same
    switch as address lookup: with GEOCODE_ENABLED off, nothing leaves the
    machine and the pantry map's zip search reports that it could not locate
    the zip.
    """
    if not GEOCODE_ENABLED:
        return None
    if zip_code in _zip_cache:
        return _zip_cache[zip_code]
    coords = _lookup({"postalcode": zip_code, "country": "us"})
    if coords:
        _zip_cache[zip_code] = coords
    return coords


def distance_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance. Straight-line, not driving distance: it ranks
    nearby shelves correctly, which is all the side panel uses it for."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 3958.8 * 2 * math.asin(math.sqrt(h))

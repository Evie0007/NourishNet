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
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

GEOCODE_ENABLED = os.getenv("GEOCODE_ENABLED", "false").lower() in ("1", "true", "yes")
GEOCODE_URL = os.getenv("GEOCODE_URL", "https://nominatim.openstreetmap.org/search")
GEOCODE_TIMEOUT = float(os.getenv("GEOCODE_TIMEOUT_SECONDS", "5"))


def geocode_address(address: Optional[str]) -> Optional[tuple[float, float]]:
    """(latitude, longitude) for a free-text address, or None."""
    if not GEOCODE_ENABLED or not address or not address.strip():
        return None

    query = urllib.parse.urlencode({"q": address.strip(), "format": "json", "limit": 1})
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

"""City geocoding utilities using geopy Nominatim (free, no API key)."""

import json
import os
import re
import time
from urllib.parse import quote_plus

from geopy.geocoders import Nominatim


_geocoder = Nominatim(user_agent="gym-lead-scraper/1.0")
_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", ".geocache.json")


def _load_cache() -> dict:
    try:
        with open(_CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict):
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
    with open(_CACHE_FILE, "w") as f:
        json.dump(cache, f)


def geocode_city(city_str: str) -> dict:
    """Convert a city string like 'Ashburn, VA' into geocoding data.

    Returns dict with keys:
        lat, lng          - float coordinates
        city, state       - parsed components
        url_encoded       - URL-encoded string for MindBody
        slug              - lowercase slug for ClassPass
    """
    cache = _load_cache()
    if city_str in cache:
        return cache[city_str]

    time.sleep(1)  # Respect Nominatim rate limit (1 req/sec)
    location = _geocoder.geocode(city_str, addressdetails=True, exactly_one=True)
    if not location:
        raise ValueError(f"Could not geocode city: {city_str}")

    lat = location.latitude
    lng = location.longitude

    # Parse city and state from the input string or geocoder response
    address = location.raw.get("address", {})
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or city_str.split(",")[0].strip()
    )
    state = (
        address.get("state")
        or (city_str.split(",")[1].strip() if "," in city_str else "")
    )

    # URL-encoded for MindBody search
    url_encoded = quote_plus(city_str)

    # Lowercase slug for ClassPass (e.g., "Ashburn, VA" -> "ashburn-va")
    slug = re.sub(r"[^a-z0-9]+", "-", city_str.lower()).strip("-")

    result = {
        "lat": lat,
        "lng": lng,
        "city": city,
        "state": state,
        "url_encoded": url_encoded,
        "slug": slug,
    }

    cache[city_str] = result
    _save_cache(cache)

    return result

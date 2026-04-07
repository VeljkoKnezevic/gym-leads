"""Google Maps scraper via Serper.dev Maps API — no browser needed."""

import os
import time
from pathlib import Path
from typing import Optional

import requests

from .base import BaseScraper, Lead

# Load .env if present (for SERPER_API_KEY / SERPAPI_KEY)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SERPER_MAPS_URL = "https://google.serper.dev/maps"

# Focused queries — broad terms catch 80%+ of leads, niches add the rest.
# 5 queries × up to 3 pages = 15 API calls per city.
GYM_QUERIES = [
    "gym near me",
    "fitness studio",
    "fitness center",
    "yoga studio",
    "pilates barre studio",
]

RESULTS_PER_PAGE = 20
MAX_PAGES_PER_QUERY = 3  # 3 × 20 = 60 results per query


class SerpApiScraper(BaseScraper):
    source_name = "google_maps"

    def _run_browser(self) -> list[Lead]:
        return self._scrape(None)  # skip browser, use requests

    def _scrape(self, page) -> list[Lead]:
        api_key = os.environ.get("SERPER_API_KEY", "")
        if not api_key:
            # Fall back to SerpAPI if available
            api_key_serpapi = os.environ.get("SERPAPI_KEY", "")
            if api_key_serpapi:
                return self._scrape_serpapi(api_key_serpapi)
            print("  [google_maps] SERPER_API_KEY not set, skipping")
            return []

        all_businesses: list[dict] = []
        seen_place_ids: set[str] = set()

        for query in GYM_QUERIES:
            for page_num in range(MAX_PAGES_PER_QUERY):
                payload = {
                    "q": query,
                    "ll": f"@{self.geo['lat']},{self.geo['lng']},12z",
                    "num": RESULTS_PER_PAGE,
                    "page": page_num + 1,
                }
                headers = {
                    "X-API-KEY": api_key,
                    "Content-Type": "application/json",
                }

                data = None
                for attempt in range(3):
                    try:
                        resp = requests.post(
                            SERPER_MAPS_URL, json=payload,
                            headers=headers, timeout=30,
                        )
                        data = resp.json()
                        break
                    except Exception as e:
                        wait = 10 * (attempt + 1)
                        print(f"  [google_maps] '{query}' p{page_num+1} attempt {attempt+1} failed: {e} — waiting {wait}s")
                        time.sleep(wait)

                if data is None:
                    print(f"  [google_maps] Skipping '{query}' p{page_num+1} after 3 failures")
                    break

                results = data.get("places", [])
                if not results:
                    break

                new = 0
                for biz in results:
                    place_id = biz.get("placeId") or biz.get("cid", "")
                    if place_id and place_id in seen_place_ids:
                        continue
                    if place_id:
                        seen_place_ids.add(place_id)
                    all_businesses.append(biz)
                    new += 1

                print(
                    f"  [google_maps] '{query}' p{page_num + 1}: "
                    f"{len(results)} results ({new} new)"
                )

                if len(results) < RESULTS_PER_PAGE:
                    break

        leads = [self._parse_serper(b) for b in all_businesses]
        leads = [l for l in leads if l]
        print(f"  [google_maps] Found {len(leads)} leads")
        return leads

    def _parse_serper(self, b: dict) -> Optional[Lead]:
        name = b.get("title", "").strip()
        if not name:
            return None

        address_raw = b.get("address", "")
        parts = [p.strip() for p in address_raw.split(",")]
        if len(parts) >= 3:
            address = ", ".join(parts[:-2])
            city = parts[-2].strip()
            state_zip = parts[-1].strip().split()
            state = state_zip[0] if state_zip else self.geo["state"]
        elif len(parts) == 2:
            address = parts[0]
            city = self.geo["city"]
            state = self.geo["state"]
        else:
            address = address_raw
            city = self.geo["city"]
            state = self.geo["state"]

        gym_type = b.get("type", "Fitness")
        phone = b.get("phoneNumber", "")
        website = b.get("website", "")

        return Lead(
            name=name,
            address=address,
            city=city,
            state=state,
            phone=phone,
            website=website,
            type=gym_type,
            source="google_maps",
        )

    # --- SerpAPI fallback (legacy) ---

    def _scrape_serpapi(self, api_key: str) -> list[Lead]:
        """Fallback to SerpAPI if SERPER_API_KEY is not available."""
        print("  [google_maps] Using SerpAPI fallback")
        all_businesses: list[dict] = []
        seen_place_ids: set[str] = set()

        for query in GYM_QUERIES:
            for page_num in range(MAX_PAGES_PER_QUERY):
                offset = page_num * 20
                params = {
                    "engine": "google_maps",
                    "q": query,
                    "ll": f"@{self.geo['lat']},{self.geo['lng']},12z",
                    "type": "search",
                    "start": offset,
                    "api_key": api_key,
                }

                data = None
                for attempt in range(3):
                    try:
                        resp = requests.get(
                            "https://serpapi.com/search",
                            params=params, timeout=30,
                        )
                        data = resp.json()
                        break
                    except Exception as e:
                        wait = 10 * (attempt + 1)
                        print(f"  [google_maps] '{query}' p{page_num+1} attempt {attempt+1} failed: {e} — waiting {wait}s")
                        time.sleep(wait)

                if data is None:
                    break

                results = data.get("local_results", [])
                if not results:
                    break

                new = 0
                for biz in results:
                    place_id = biz.get("place_id") or biz.get("data_id", "")
                    if place_id and place_id in seen_place_ids:
                        continue
                    if place_id:
                        seen_place_ids.add(place_id)
                    all_businesses.append(biz)
                    new += 1

                print(
                    f"  [google_maps] '{query}' p{page_num + 1}: "
                    f"{len(results)} results ({new} new)"
                )

                if len(results) < 20:
                    break

        leads = [self._parse_serpapi(b) for b in all_businesses]
        leads = [l for l in leads if l]
        print(f"  [google_maps] Found {len(leads)} leads")
        return leads

    def _parse_serpapi(self, b: dict) -> Optional[Lead]:
        name = b.get("title", "").strip()
        if not name:
            return None

        address_raw = b.get("address", "")
        parts = [p.strip() for p in address_raw.split(",")]
        if len(parts) >= 3:
            address = ", ".join(parts[:-2])
            city = parts[-2].strip()
            state_zip = parts[-1].strip().split()
            state = state_zip[0] if state_zip else self.geo["state"]
        elif len(parts) == 2:
            address = parts[0]
            city = self.geo["city"]
            state = self.geo["state"]
        else:
            address = address_raw
            city = self.geo["city"]
            state = self.geo["state"]

        gym_type = b.get("type", "Fitness")
        phone = b.get("phone", "")
        website = b.get("website", "")

        return Lead(
            name=name,
            address=address,
            city=city,
            state=state,
            phone=phone,
            website=website,
            type=gym_type,
            source="google_maps",
        )

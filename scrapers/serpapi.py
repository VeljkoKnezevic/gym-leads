"""SerpAPI Google Maps scraper — no browser needed, phone in search response."""

import os
import time

import requests

from .base import BaseScraper, Lead

SERPAPI_SEARCH_URL = "https://serpapi.com/search"

# Broad queries that cover the full fitness landscape without burning too many
# of the 250 free-tier searches/month. Each query paginates up to 3 pages
# (60 results max), so 5 queries = 15 searches per city run (~16 city runs/month).
GYM_QUERIES = [
    "gym",
    "yoga studio",
    "martial arts",
    "fitness studio",
    "boxing gym",
]

MAX_PAGES_PER_QUERY = 3  # 3 × 20 = 60 results per query, 15 total API calls/city


class SerpApiScraper(BaseScraper):
    source_name = "google_maps"

    def _run_browser(self) -> list[Lead]:
        return self._scrape(None)  # skip browser, use requests

    def _scrape(self, page) -> list[Lead]:
        api_key = os.environ.get("SERPAPI_KEY", "")
        if not api_key:
            print("  [google_maps] SERPAPI_KEY not set, skipping")
            return []

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

                # Retry this single request up to 3 times before moving on
                data = None
                for attempt in range(3):
                    try:
                        resp = requests.get(
                            SERPAPI_SEARCH_URL, params=params, timeout=30
                        )
                        data = resp.json()
                        break
                    except Exception as e:
                        wait = 10 * (attempt + 1)
                        print(f"  [google_maps] '{query}' p{page_num+1} attempt {attempt+1} failed: {e} — waiting {wait}s")
                        time.sleep(wait)

                if data is None:
                    print(f"  [google_maps] Skipping '{query}' p{page_num+1} after 3 failures")
                    break  # skip remaining pages for this query

                results = data.get("local_results", [])
                if not results:
                    break  # no more pages for this query

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
                    break  # last page for this query

        leads = [self._parse(b) for b in all_businesses]
        leads = [l for l in leads if l]
        print(f"  [google_maps] Found {len(leads)} leads")
        return leads

    def _parse(self, b: dict) -> Lead | None:
        name = b.get("title", "").strip()
        if not name:
            return None

        address_raw = b.get("address", "")
        # SerpAPI address: "123 Main St, Charleston, SC 29401"
        # Split off city/state from the street address
        parts = [p.strip() for p in address_raw.split(",")]
        if len(parts) >= 3:
            address = ", ".join(parts[:-2])
            city = parts[-2].strip()
            # Last part may be "SC 29401" — take just the state code
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

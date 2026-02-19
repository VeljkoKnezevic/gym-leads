"""MindBody scraper via prod-mkt-gateway API with full pagination."""

import json

from playwright.sync_api import Page, Response

from .base import BaseScraper, Lead

API_URL = "https://prod-mkt-gateway.mindbody.io/v1/search/locations"
PAGE_SIZE = 50

# Sub-categories that are clearly not fitness facilities (massage, beauty, medical).
# Some businesses self-register under Fitness even when they're not gyms, so
# this second layer of filtering catches what the API-level categoryTypes can't.
NON_FITNESS_CATEGORIES = {
    "massage", "acupuncture", "face treatments", "med spa",
    "nails", "hair", "waxing", "tanning", "tattoo",
}


class MindBodyScraper(BaseScraper):
    source_name = "mindbody"

    def _scrape(self, page: Page) -> list[Lead]:
        city_encoded = self.geo["url_encoded"]
        city = self.geo["city"]
        state = self.geo["state"]
        lat = self.geo["lat"]
        lng = self.geo["lng"]

        # Load the page first to establish cookies/session
        search_url = f"https://www.mindbodyonline.com/explore/search?location={city_encoded}"
        print(f"  [mindbody] Navigating to: {search_url}")
        page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)

        # Fetch all pages via direct API calls
        all_items = []
        page_num = 1
        total_found = None

        while True:
            payload = {
                "sort": "distance",
                "page": {"size": PAGE_SIZE, "number": page_num},
                "filter": {
                    "radius": 40233.6,  # ~25 miles in meters
                    "latitude": lat,
                    "longitude": lng,
                    "categoryTypes": ["Fitness"],
                },
            }

            resp = page.evaluate(
                """(payload) => {
                    return fetch('%s', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(payload)
                    }).then(r => r.json())
                }"""
                % API_URL,
                payload,
            )

            items = resp.get("data", [])
            meta = resp.get("meta", {})
            if total_found is None:
                total_found = meta.get("found", 0)
                print(f"  [mindbody] API reports {total_found} total locations")

            all_items.extend(items)
            print(f"  [mindbody] Page {page_num}: got {len(items)} (total so far: {len(all_items)})")

            if not items or len(all_items) >= total_found:
                break

            page_num += 1
            self.human_delay(0.5, 1.5)

        leads = self._parse_items(all_items, city, state)
        print(f"  [mindbody] Found {len(leads)} leads")
        return leads

    def _parse_items(self, items: list[dict], city: str, state: str) -> list[Lead]:
        """Parse leads from MindBody location items."""
        leads = []
        seen_ids = set()

        for item in items:
            item_id = item.get("id")
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            attrs = item.get("attributes", {})
            name = attrs.get("name", "").strip()
            if not name:
                continue

            slug = attrs.get("slug", "")
            website = f"https://www.mindbodyonline.com/explore/locations/{slug}" if slug else ""

            categories = attrs.get("categories", [])
            gym_type = categories[0] if categories else "Fitness"

            if gym_type.lower() in NON_FITNESS_CATEGORIES:
                continue

            leads.append(Lead(
                name=name,
                address=attrs.get("address", ""),
                city=attrs.get("city", city),
                state=attrs.get("state", state),
                phone=attrs.get("phone", ""),
                website=website,
                type=gym_type,
                source="mindbody",
            ))

        return leads

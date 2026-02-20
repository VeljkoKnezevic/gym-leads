"""HYROX partner gym scraper via WP Store Locator form interaction."""

import html

from playwright.sync_api import Page

from .base import BaseScraper, Lead


class HyroxScraper(BaseScraper):
    source_name = "hyrox"

    # ~50 mile radius in degrees for filtering
    RADIUS_DEG = 0.75

    def _scrape(self, page: Page) -> list[Lead]:
        lat = self.geo["lat"]
        lng = self.geo["lng"]
        city = self.geo["city"]
        state = self.geo["state"]

        print(f"  [hyrox] Searching for partner gyms near {city}, {state}")

        # Capture AJAX responses
        captured_data = []

        def handle_response(response):
            if "admin-ajax.php" in response.url and response.ok:
                try:
                    data = response.json()
                    if isinstance(data, list) and len(data) > 0:
                        captured_data.extend(data)
                except Exception:
                    pass

        page.on("response", handle_response)

        # Navigate to the gym finder page
        page.goto("https://gyms.elbnetz.cloud/gyms", wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(2000)

        # Enter the city name in the search field and trigger search
        search_input = page.locator("#wpsl-search-input")
        if search_input.count() > 0:
            search_input.fill(f"{city}, {state}")
            page.wait_for_timeout(500)

            # Click search button
            search_btn = page.locator("#wpsl-search-btn")
            if search_btn.count() > 0:
                search_btn.click()
                print(f"  [hyrox] Triggered search for '{city}, {state}'")
                page.wait_for_timeout(5000)  # Wait for AJAX response

        # If no results captured via event, try extracting from page JS
        if not captured_data:
            print("  [hyrox] No AJAX response captured, trying alternate method...")
            # Try getting wpslSettings or marker data from the page
            try:
                js_data = page.evaluate("""() => {
                    if (typeof wpslMap_0 !== 'undefined' && wpslMap_0.storeMarkers) {
                        return wpslMap_0.storeMarkers;
                    }
                    if (typeof wpslSettings !== 'undefined') {
                        return { settings: true };
                    }
                    return null;
                }""")
                if js_data and isinstance(js_data, list):
                    captured_data = js_data
            except Exception:
                pass

        if not captured_data:
            print("  [hyrox] No gym data found")
            return []

        print(f"  [hyrox] Got {len(captured_data)} partner gyms from API")

        # Filter to nearby gyms based on lat/lng
        nearby = []
        for item in captured_data:
            try:
                item_lat = float(item.get("lat", 0))
                item_lng = float(item.get("lng", 0))
                if abs(item_lat - lat) <= self.RADIUS_DEG and abs(item_lng - lng) <= self.RADIUS_DEG:
                    nearby.append(item)
            except (ValueError, TypeError):
                continue

        print(f"  [hyrox] Found {len(nearby)} partner gyms within ~50mi of {city}, {state}")

        leads = self._parse_results(nearby, city, state)
        print(f"  [hyrox] Parsed {len(leads)} leads")
        return leads

    def _parse_results(self, results: list[dict], city: str, state: str) -> list[Lead]:
        """Parse WPSL results into Lead objects."""
        leads = []

        for item in results:
            name = item.get("store", "").strip()
            if not name:
                continue

            # Decode HTML entities (e.g., &#038; -> &)
            name = html.unescape(name)

            # Build address from components
            address_parts = []
            if item.get("address"):
                address_parts.append(html.unescape(item["address"].strip()))
            if item.get("address2"):
                address_parts.append(html.unescape(item["address2"].strip()))
            address = ", ".join(address_parts)

            # Get city/state from result or fall back to search location
            gym_city = html.unescape(item.get("city", "").strip()) or city
            gym_state = html.unescape(item.get("state", "").strip()) or state

            # Get phone, clean it up
            phone = item.get("phone", "").strip()

            # Get website URL
            website = item.get("url", "").strip()

            leads.append(Lead(
                name=name,
                address=address,
                city=gym_city,
                state=gym_state,
                phone=phone,
                website=website,
                type="HYROX Partner",
                source="hyrox",
            ))

        return leads

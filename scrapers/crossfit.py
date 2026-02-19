"""CrossFit affiliate scraper via affiliates.json GeoJSON interception."""

from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.sync_api import sync_playwright, Page

from .base import BaseScraper, Lead, USER_AGENT

ENRICH_WORKERS = 5


def _fetch_phone(url: str, headless: bool) -> str:
    """Fetch phone from a CrossFit detail page using an isolated browser instance."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(user_agent=USER_AGENT)
        page.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in ("image", "font", "media")
                else route.continue_()
            ),
        )
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)  # let React finish rendering phone/details
            return BaseScraper.extract_phone(page)
        except Exception:
            return ""
        finally:
            browser.close()


class CrossFitScraper(BaseScraper):
    source_name = "crossfit"

    # ~30 mile radius in degrees (rough approximation)
    RADIUS_DEG = 0.5

    def _scrape(self, page: Page) -> list[Lead]:
        lat = self.geo["lat"]
        lng = self.geo["lng"]
        city = self.geo["city"]
        state = self.geo["state"]

        map_url = f"https://www.crossfit.com/map/?type=search&lat={lat}&lng={lng}&zoom=10"
        print(f"  [crossfit] Navigating to map: lat={lat}, lng={lng}")

        affiliates_geojson = {}
        try:
            with page.expect_response(
                lambda r: "affiliates.json" in r.url and r.ok,
                timeout=30000,
            ) as resp_info:
                page.goto(map_url, wait_until="domcontentloaded", timeout=45000)
            affiliates_geojson = resp_info.value.json()
        except Exception as e:
            print(f"  [crossfit] expect_response failed ({e}), falling back to timed wait...")
            # Fallback: wait for the event-based handler to populate
            page.wait_for_timeout(15000)

        if not affiliates_geojson:
            print("  [crossfit] affiliates.json not captured")
            return []

        features = affiliates_geojson.get("features", [])
        print(f"  [crossfit] Got {len(features)} total affiliates worldwide")

        # Filter to nearby affiliates
        leads = []
        for feature in features:
            props = feature.get("properties", {})
            coords = feature.get("geometry", {}).get("coordinates", [])
            if len(coords) < 2:
                continue

            f_lng, f_lat = coords[0], coords[1]
            if abs(f_lat - lat) > self.RADIUS_DEG or abs(f_lng - lng) > self.RADIUS_DEG:
                continue

            name = props.get("name", "").strip()
            if not name:
                continue

            slug = props.get("slug", "")
            website = f"https://www.crossfit.com{slug}" if slug else ""

            leads.append(Lead(
                name=name,
                address=props.get("address", props.get("street", "")),
                city=props.get("city", city),
                state=props.get("state", state),
                phone="",
                website=website,
                type="CrossFit",
                source="crossfit",
            ))

        print(f"  [crossfit] Found {len(leads)} affiliates near {city}, {state}")

        if self.enrich:
            self._enrich_phone_numbers(leads)

        return leads

    def _enrich_phone_numbers(self, leads: list[Lead]):
        """Visit CrossFit affiliate detail pages in parallel to grab phone numbers."""
        to_enrich = [l for l in leads if l.website]
        print(f"  [crossfit] Enriching {len(to_enrich)} leads ({ENRICH_WORKERS} workers)...")

        with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as executor:
            futures = {
                executor.submit(_fetch_phone, lead.website, self.headless): lead
                for lead in to_enrich
            }
            for future in as_completed(futures):
                lead = futures[future]
                try:
                    phone = future.result()
                    if phone:
                        lead.phone = phone
                        print(f"  [crossfit]   {lead.name}: {phone}")
                except Exception:
                    pass

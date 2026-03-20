"""ClassPass scraper — stealth browser + network-interception fallback."""

from playwright.sync_api import Page, sync_playwright

from .base import BaseScraper, Lead, USER_AGENT

# Comprehensive JS stealth patch injected before any page script runs
_STEALTH_SCRIPT = """
// Hide webdriver flag
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// Realistic window dimensions
Object.defineProperty(window, 'outerWidth',  {get: () => 1366});
Object.defineProperty(window, 'outerHeight', {get: () => 768});
// Fake plugin list (empty list is a bot signal)
Object.defineProperty(navigator, 'plugins', {
    get: () => ({0: {filename:'internal-pdf-viewer'}, length: 1})
});
// Language
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
// Chrome object (absent in headless)
window.chrome = {runtime: {}, app: {}, csi: function(){}, loadTimes: function(){}};
// Permissions API
if (navigator.permissions && navigator.permissions.query) {
    const _orig = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = p =>
        p.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : _orig(p);
}
"""


class ClassPassScraper(BaseScraper):
    source_name = "classpass"

    # ------------------------------------------------------------------ #
    # Browser setup — override base to apply stealth & allow all resources
    # ------------------------------------------------------------------ #

    def _run_browser(self) -> list[Lead]:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 768},
                locale="en-US",
                timezone_id="America/New_York",
            )
            context.add_init_script(_STEALTH_SCRIPT)
            page = context.new_page()
            # Allow all resource types — blocking fonts/images can prevent
            # React from mounting on some SPAs
            try:
                leads = self._scrape(page)
                return leads
            finally:
                context.close()
                browser.close()

    # ------------------------------------------------------------------ #
    # Main scrape — try form; fall back to API interception
    # ------------------------------------------------------------------ #

    def _scrape(self, page: Page) -> list[Lead]:
        city = self.geo["city"]
        state = self.geo["state"]

        # Capture every JSON response from classpass.com
        api_responses: list[dict] = []

        def on_response(response):
            if "classpass.com" not in response.url:
                return
            ct = response.headers.get("content-type", "")
            if response.status == 200 and "json" in ct:
                try:
                    api_responses.append({"url": response.url, "data": response.json()})
                except Exception:
                    pass

        page.on("response", on_response)

        print(f"  [classpass] Navigating to ClassPass search...")
        page.goto("https://classpass.com/search", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        # Diagnostics — tell us exactly what page we landed on
        print(f"  [classpass] URL:   {page.url}")
        print(f"  [classpass] Title: {page.title()}")
        all_inputs = page.query_selector_all("input")
        print(f"  [classpass] Inputs on page: {len(all_inputs)}")

        self._dismiss_consent(page)
        found = self._set_location(page, city)

        # Give the page time to fire venue API calls after location is set
        page.wait_for_timeout(5000)

        # ---- Try API-response venue data first ----
        leads = self._parse_api_responses(api_responses, city, state)
        if leads:
            if self.enrich:
                leads = self._enrich_phone_numbers(page, leads)
            print(f"  [classpass] Found {len(leads)} leads (API)")
            return leads

        if not found:
            print(f"  [classpass] Found 0 leads")
            return []

        # ---- Fall back to DOM scraping ----
        for _ in range(4):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            self.human_delay(2, 3)
            page.wait_for_timeout(2000)

        leads = self._scrape_venue_cards(page, city, state)
        if leads and self.enrich:
            leads = self._enrich_phone_numbers(page, leads)

        print(f"  [classpass] Found {len(leads)} leads")
        return leads

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _dismiss_consent(self, page: Page):
        try:
            btn = page.query_selector("#truste-consent-button")
            if btn and btn.is_visible():
                with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                    btn.click()
                page.wait_for_timeout(3000)
        except Exception:
            page.wait_for_timeout(3000)

    def _set_location(self, page: Page, city_name: str) -> bool:
        try:
            page.wait_for_selector("input", timeout=12000)
        except Exception:
            pass
        page.wait_for_timeout(1000)

        SELECTORS = [
            '[role="searchbox"]',
            '[role="combobox"]',
            'input[placeholder*="City" i]',
            'input[placeholder*="location" i]',
            'input[placeholder*="neighborhood" i]',
            'input[placeholder*="search" i]',
            'input[type="search"]',
            'input[type="text"]',
            'input',
        ]

        search_input = None
        for sel in SELECTORS:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    search_input = el
                    break
            except Exception:
                continue

        if not search_input:
            print("  [classpass] Could not find location input")
            return False

        print(f"  [classpass] Setting location to: {city_name}")
        search_input.click()
        page.wait_for_timeout(500)
        search_input.fill("")
        search_input.type(city_name, delay=80)
        page.wait_for_timeout(3000)

        try:
            page.wait_for_selector("[role=option]", timeout=5000)
            suggestions = page.query_selector_all("[role=option]")
            if suggestions:
                with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                    suggestions[0].click()
                page.wait_for_timeout(4000)
                print(f"  [classpass] Navigated to: {page.url}")
                return True
        except Exception:
            pass

        page.keyboard.press("Enter")
        page.wait_for_timeout(6000)
        print(f"  [classpass] URL after Enter: {page.url}")
        return True

    def _parse_api_responses(self, responses: list[dict], city: str, state: str) -> list[Lead]:
        """Extract leads from captured ClassPass JSON API responses."""
        leads = []
        seen = set()
        VENUE_KEYS = ("results", "venues", "studios", "listings", "items", "data")

        for item in responses:
            data = item["data"]
            url = item["url"]
            for key in VENUE_KEYS:
                entries = data.get(key)
                if not isinstance(entries, list) or not entries:
                    continue
                first = entries[0]
                if not isinstance(first, dict):
                    continue
                # Must look like a venue (has a name-like field)
                name_field = next((f for f in ("name", "title", "venue_name") if f in first), None)
                if not name_field:
                    continue
                print(f"  [classpass] Parsing {len(entries)} entries from {url[:70]}")
                for entry in entries:
                    name = (entry.get(name_field) or "").strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    address = (entry.get("address") or entry.get("street_address") or "").strip()
                    phone = (entry.get("phone") or entry.get("phone_number") or "").strip()
                    website = (entry.get("website") or entry.get("url") or "").strip()
                    gym_type = (entry.get("type") or entry.get("category") or "Fitness").strip()
                    leads.append(Lead(
                        name=name, address=address, city=city, state=state,
                        phone=phone, website=website, type=gym_type, source="classpass",
                    ))
                break  # found the venue array for this response

        return leads

    def _scrape_venue_cards(self, page: Page, city: str, state: str) -> list[Lead]:
        leads = []
        seen_names = set()

        cards = page.query_selector_all('[data-testid="VenueItem"]')
        if not cards:
            cards = page.query_selector_all('[data-component="VenueItem"]')

        for card in cards:
            try:
                name_el = card.query_selector('[data-qa="VenueItem.name"]')
                if not name_el:
                    name_el = card.query_selector("h2 a")
                name = name_el.inner_text().strip() if name_el else ""
                if not name or name in seen_names:
                    continue
                seen_names.add(name)

                addr_el = card.query_selector('[data-qa="VenueItem.location"]')
                address = addr_el.inner_text().strip() if addr_el else ""

                type_el = card.query_selector('[data-qa="VenueItem.activities"]')
                gym_type = type_el.inner_text().strip() if type_el else "Fitness"

                link = card.query_selector('a[href*="/studios/"]')
                website = ""
                if link:
                    href = link.get_attribute("href") or ""
                    website = f"https://classpass.com{href}" if href.startswith("/") else href

                leads.append(Lead(
                    name=name, address=address, city=city, state=state,
                    phone="", website=website, type=gym_type, source="classpass",
                ))
            except Exception:
                continue

        return leads

    def _enrich_phone_numbers(self, page: Page, leads: list[Lead]) -> list[Lead]:
        to_enrich = [l for l in leads if not l.phone and l.website and "classpass.com" in l.website]
        if not to_enrich:
            return leads
        print(f"  [classpass] Enriching {len(to_enrich)} leads from detail pages...")

        for lead in to_enrich:
            try:
                page.goto(lead.website, wait_until="domcontentloaded", timeout=20000)
                self.human_delay(2, 4)
                try:
                    btn = page.query_selector("#truste-consent-button")
                    if btn and btn.is_visible():
                        btn.click(timeout=3000)
                        page.wait_for_timeout(1000)
                except Exception:
                    pass
                lead.phone = self.extract_phone(page)
                if "classpass.com" in lead.website:
                    skip = ("google.com", "youtube.com", "facebook.com", "instagram.com",
                            "twitter.com", "yelp.com", "classpass.com", "theknot.com",
                            "linkedin.com", "tiktok.com")
                    for a in page.query_selector_all("a[href*='http'][target='_blank']"):
                        href = a.get_attribute("href") or ""
                        if href and not any(d in href for d in skip):
                            lead.website = href
                            break
            except Exception:
                continue

        return leads

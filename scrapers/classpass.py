"""ClassPass scraper via DOM scraping with location input interaction."""

from playwright.sync_api import Page

from .base import BaseScraper, Lead


class ClassPassScraper(BaseScraper):
    source_name = "classpass"

    def _scrape(self, page: Page) -> list[Lead]:
        city = self.geo["city"]
        state = self.geo["state"]

        # Build direct ClassPass URL from geo slug.
        # geo["slug"] is e.g. "bozeman-mt" or "fort-wayne-in".
        # ClassPass uses "bozeman--mt" / "fort-wayne--in" (double-dash before state).
        slug = self.geo["slug"]
        city_part, state_part = slug.rsplit("-", 1)
        classpass_slug = f"{city_part}--{state_part}"
        url = f"https://classpass.com/classes/{classpass_slug}"

        print(f"  [classpass] Navigating to: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)

        # Dismiss cookie consent banner (blocks clicks if present)
        self._dismiss_consent(page)

        # Scroll to load more venue cards
        for i in range(4):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            self.human_delay(2, 3)
            page.wait_for_timeout(2000)

        # Scrape venue cards from the DOM
        leads = self._scrape_venue_cards(page, city, state)

        if leads and self.enrich:
            leads = self._enrich_phone_numbers(page, leads)

        print(f"  [classpass] Found {len(leads)} leads")
        return leads

    def _dismiss_consent(self, page: Page):
        """Dismiss TrustArc cookie consent banner if present."""
        try:
            btn = page.query_selector("#truste-consent-button")
            if btn and btn.is_visible():
                with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                    btn.click()
                page.wait_for_timeout(3000)
        except Exception:
            page.wait_for_timeout(3000)

    def _scrape_venue_cards(self, page: Page, city: str, state: str) -> list[Lead]:
        """Scrape venue data from VenueItem cards in the DOM."""
        leads = []
        seen_names = set()

        cards = page.query_selector_all('[data-testid="VenueItem"]')
        if not cards:
            cards = page.query_selector_all('[data-component="VenueItem"]')

        for card in cards:
            try:
                # Name
                name_el = card.query_selector('[data-qa="VenueItem.name"]')
                if not name_el:
                    name_el = card.query_selector("h2 a")
                name = name_el.inner_text().strip() if name_el else ""
                if not name or name in seen_names:
                    continue
                seen_names.add(name)

                # Address/location
                addr_el = card.query_selector('[data-qa="VenueItem.location"]')
                address = addr_el.inner_text().strip() if addr_el else ""

                # Activity type
                type_el = card.query_selector('[data-qa="VenueItem.activities"]')
                gym_type = type_el.inner_text().strip() if type_el else "Fitness"

                # Studio detail page link
                link = card.query_selector('a[href*="/studios/"]')
                website = ""
                if link:
                    href = link.get_attribute("href") or ""
                    if href.startswith("/"):
                        website = f"https://classpass.com{href}"
                    else:
                        website = href

                leads.append(Lead(
                    name=name,
                    address=address,
                    city=city,
                    state=state,
                    phone="",
                    website=website,
                    type=gym_type,
                    source="classpass",
                ))
            except Exception:
                continue

        return leads

    def _enrich_phone_numbers(self, page: Page, leads: list[Lead]) -> list[Lead]:
        """Visit every ClassPass detail page to grab phone numbers and external websites."""
        to_enrich = [l for l in leads if not l.phone and l.website and "classpass.com" in l.website]
        print(f"  [classpass] Enriching {len(to_enrich)} leads from detail pages...")

        for lead in to_enrich:
            try:
                print(f"  [classpass] Enriching: {lead.name}")
                page.goto(lead.website, wait_until="domcontentloaded", timeout=20000)
                self.human_delay(2, 4)

                # Dismiss consent banner if it reappears
                try:
                    btn = page.query_selector("#truste-consent-button")
                    if btn and btn.is_visible():
                        btn.click(timeout=3000)
                        page.wait_for_timeout(1000)
                except Exception:
                    pass

                # Extract phone via shared helper
                lead.phone = self.extract_phone(page)

                # Try to get the studio's own website (skip maps, social, etc.)
                if "classpass.com" in lead.website:
                    skip_domains = ("google.com", "youtube.com", "facebook.com",
                                    "instagram.com", "twitter.com", "yelp.com",
                                    "classpass.com", "theknot.com", "linkedin.com",
                                    "tiktok.com")
                    for link_el in page.query_selector_all("a[href*='http'][target='_blank']"):
                        href = link_el.get_attribute("href") or ""
                        if href and not any(d in href for d in skip_domains):
                            lead.website = href
                            break
            except Exception:
                continue

        return leads

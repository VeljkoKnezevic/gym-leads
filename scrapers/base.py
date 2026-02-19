"""Base scraper with Lead dataclass, browser setup, and retry logic."""

import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Optional

from playwright.sync_api import sync_playwright, Page, BrowserContext


@dataclass
class Lead:
    name: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    phone: str = ""
    website: str = ""
    type: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


CSV_COLUMNS = ["name", "address", "city", "state", "phone", "website", "type", "source"]


def normalize_phone(raw: str) -> str:
    """Normalize a US phone number to (XXX) XXX-XXXX format.

    Handles: '5712231615', '+1 571 223 1615', '(571) 223-1615', '+15712231615', etc.
    Returns empty string if not a valid 10-digit US number.
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    # Strip leading 1 (US country code)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return raw  # Return as-is if we can't parse it
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"

# Realistic browser fingerprint
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


class BaseScraper(ABC):
    """Abstract base for all gym scrapers."""

    source_name: str = "unknown"
    max_retries: int = 3
    backoff_delays: list = [5, 15, 30]

    def __init__(self, geo_data: dict, headless: bool = True, enrich: bool = True):
        self.geo = geo_data
        self.headless = headless
        self.enrich = enrich
        self.leads: list[Lead] = []

    @abstractmethod
    def _scrape(self, page: Page) -> list[Lead]:
        """Implement site-specific scraping logic."""
        ...

    def run(self) -> list[Lead]:
        """Launch browser, run scraper with retry logic, return leads."""
        for attempt in range(self.max_retries):
            try:
                return self._run_browser()
            except Exception as e:
                delay = self.backoff_delays[min(attempt, len(self.backoff_delays) - 1)]
                if attempt < self.max_retries - 1:
                    print(f"  [{self.source_name}] Attempt {attempt + 1} failed: {e}")
                    print(f"  [{self.source_name}] Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"  [{self.source_name}] All {self.max_retries} attempts failed: {e}")
        return []

    def _run_browser(self) -> list[Lead]:
        """Set up browser context and run scraper."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
            )
            # Hide headless/automation signals that sites use for bot detection
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = context.new_page()

            # Block images, fonts, and media to speed up loading
            page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in ("image", "font", "media")
                    else route.continue_()
                ),
            )

            try:
                leads = self._scrape(page)
                return leads
            finally:
                context.close()
                browser.close()

    @staticmethod
    def extract_phone(page: Page) -> str:
        """Extract phone number from current page via tel: link or regex fallback."""
        tel_link = page.query_selector("a[href^='tel:']")
        if tel_link:
            href = tel_link.get_attribute("href") or ""
            return href.replace("tel:", "").strip()
        body_text = page.inner_text("body")
        match = re.search(r"(\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})", body_text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def human_delay(min_sec: float = 1.0, max_sec: float = 5.0):
        """Random sleep to mimic human behavior."""
        time.sleep(random.uniform(min_sec, max_sec))

    @staticmethod
    def safe_text(page: Page, selector: str, default: str = "") -> str:
        """Safely extract text from a selector, return default if not found."""
        try:
            el = page.query_selector(selector)
            return el.inner_text().strip() if el else default
        except Exception:
            return default

    @staticmethod
    def safe_attr(page: Page, selector: str, attr: str, default: str = "") -> str:
        """Safely extract an attribute from a selector."""
        try:
            el = page.query_selector(selector)
            return el.get_attribute(attr) or default if el else default
        except Exception:
            return default

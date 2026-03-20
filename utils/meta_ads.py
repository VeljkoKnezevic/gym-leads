"""Meta Ad Library scraper — check if a business is actively running ads."""

from __future__ import annotations

import re
from urllib.parse import quote

from playwright.sync_api import sync_playwright, Browser, BrowserContext

# Reuse a single browser across calls within ad_check.py's ThreadPoolExecutor
_browser: Browser | None = None
_context: BrowserContext | None = None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


def _normalize_name(name: str) -> set[str]:
    """Normalize a business name to a set of meaningful words."""
    name = name.lower().strip()
    for word in ("llc", "inc", "corp", "the", "co"):
        name = re.sub(rf"\b{word}\b", "", name)
    name = re.sub(r"[^a-z0-9\s]", "", name)
    words = set(name.split())
    words.discard("")
    return words


def _name_matches(page_name: str, search_name: str, threshold: float = 0.4) -> bool:
    """Check if page_name is related to search_name via word overlap or containment."""
    a = _normalize_name(page_name)
    b = _normalize_name(search_name)
    if not a or not b:
        return False
    overlap = len(a & b)
    union = len(a | b)
    if (overlap / union) >= threshold:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if short and short.issubset(long):
        return True
    return False


def check_meta_ads(business_name: str) -> int:
    """Scrape Meta Ad Library for active ads matching *business_name*.

    Returns the count of active ads from matching advertisers.
    Returns 0 on any error (never raises).
    """
    if not business_name:
        return 0

    url = (
        "https://www.facebook.com/ads/library/"
        "?active_status=active&ad_type=all&country=US"
        f"&q={quote(business_name)}"
        "&search_type=keyword_unordered&media_type=all"
    )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = context.new_page()

            # Block images/media to speed things up
            page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in ("image", "font", "media")
                    else route.continue_()
                ),
            )

            page.goto(url, timeout=30000)

            # Wait for either "No ads match" or ad results to appear
            try:
                page.wait_for_selector(
                    "text='No ads match', text='See ad details', text='See summary details', text='results'",
                    timeout=10000,
                )
            except Exception:
                pass  # Timeout is fine — we'll check whatever loaded
            # Extra settle time for dynamic content
            page.wait_for_timeout(2000)

            body = page.inner_text("body")
            context.close()
            browser.close()

        # No results
        if "No ads match" in body:
            return 0

        # Extract result count if shown (e.g. "~950 results" or "1 result")
        count_match = re.search(r"[~]?(\d[\d,]*)\s+results?", body)

        # Extract advertiser names — they appear after "See ad details" or "See summary details"
        advertisers = re.findall(
            r"See (?:ad|summary) details\s*\n\s*(.+)", body
        )

        if not advertisers:
            # If we found a result count but no advertiser names, the page may
            # have loaded results that are all from unrelated advertisers
            # or the format changed. Be conservative.
            if count_match:
                total = int(count_match.group(1).replace(",", ""))
                # If very few results, they might all be relevant
                if total <= 5:
                    return total
            return 0

        # Count ads from advertisers that match the search name
        matched = sum(1 for adv in advertisers if _name_matches(adv, business_name))
        return matched

    except Exception as e:
        print(f"  [meta_ads] Error checking '{business_name}': {e}")
        return 0

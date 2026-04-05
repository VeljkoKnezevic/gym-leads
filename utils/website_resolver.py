"""Resolve platform URLs (MindBody, CrossFit, F45, etc.) to real gym websites."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# Load .env if present (for SERPAPI_KEY)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT})

SERPAPI_URL = "https://serpapi.com/search"
SERPER_URL = "https://google.serper.dev/search"

# Domains that are platform/aggregator URLs (not real gym websites)
PLATFORM_DOMAINS = {
    "mindbodyonline.com", "www.mindbodyonline.com",
    "clients.mindbodyonline.com",
    "crossfit.com", "www.crossfit.com", "map.crossfit.com",
    "wodify.com", "app.wodify.com",
    "zenplanner.com",
    "marianaiframes.com",
    "healcode.com",
}

# Domains to skip when looking for real gym websites
NOISE_DOMAINS = {
    "facebook.com", "www.facebook.com", "m.facebook.com",
    "instagram.com", "www.instagram.com",
    "twitter.com", "www.twitter.com", "x.com",
    "tiktok.com", "www.tiktok.com",
    "youtube.com", "www.youtube.com",
    "linkedin.com", "www.linkedin.com",
    "yelp.com", "www.yelp.com",
    "google.com", "www.google.com", "maps.google.com",
    "goo.gl",
    "apple.com", "apps.apple.com",
    "play.google.com",
    "pinterest.com", "www.pinterest.com",
    "tripadvisor.com", "www.tripadvisor.com",
    "bbb.org", "www.bbb.org",
    "groupon.com", "www.groupon.com",
    "classpass.com", "www.classpass.com",
    "mapquest.com", "www.mapquest.com",
    "yellowpages.com", "www.yellowpages.com",
    "nextdoor.com", "www.nextdoor.com",
    "thumbtack.com", "www.thumbtack.com",
    "patch.com", "www.patch.com",
    "wikipedia.org", "en.wikipedia.org",
    "mapbox.com", "www.mapbox.com",
    "functionalinspiredtraining.com", "www.functionalinspiredtraining.com",
    "wellnessliving.com", "www.wellnessliving.com",
    "drb.ai",
    "threads.net", "www.threads.net",
}

_ALL_SKIP = NOISE_DOMAINS | PLATFORM_DOMAINS


def _is_platform_url(url: str) -> bool:
    """Check if a URL is a known platform/aggregator URL."""
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc == d or netloc.endswith("." + d) for d in PLATFORM_DOMAINS)
    except Exception:
        return False


def _is_skip_url(url: str) -> bool:
    """Check if a URL is social media, aggregator, or other noise."""
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc == d or netloc.endswith("." + d) for d in _ALL_SKIP)
    except Exception:
        return True


def _extract_external_links(html: str, source_url: str) -> list[str]:
    """Extract external links from an HTML page, filtering out noise."""
    soup = BeautifulSoup(html, "html.parser")
    source_netloc = urlparse(source_url).netloc.lower()

    links: list[str] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue

        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue

        netloc = parsed.netloc.lower()
        if netloc == source_netloc or netloc.endswith("." + source_netloc):
            continue
        if _is_skip_url(href):
            continue

        clean = parsed._replace(fragment="").geturl()
        if clean in seen:
            continue
        seen.add(clean)

        anchor_text = (a.get_text() or "").lower()
        if any(kw in anchor_text for kw in ("visit", "website", "official", "home")):
            links.insert(0, clean)
        else:
            links.append(clean)

    return links


def _resolve_from_platform(url: str) -> str:
    """Fetch a platform page and look for outbound links to the real gym website."""
    try:
        resp = _SESSION.get(url, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return ""

    links = _extract_external_links(resp.text, resp.url)
    return links[0] if links else ""


def _search_serper(gym_name: str, city: str = "", state: str = "") -> str:
    """Use Serper.dev (free tier: 2500 queries) to find the gym's real website."""
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        return ""

    query = gym_name
    if city:
        query += f" {city}"
    if state:
        query += f" {state}"

    try:
        resp = _SESSION.post(SERPER_URL, json={"q": query, "num": 5},
                             headers={"X-API-KEY": api_key}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return ""

    for result in data.get("organic", []):
        link = result.get("link", "")
        if not link:
            continue
        if _is_skip_url(link):
            continue
        return link

    return ""


def _search_serpapi(gym_name: str, city: str = "", state: str = "") -> str:
    """Use SerpAPI as fallback search engine."""
    api_key = os.environ.get("SERPAPI_KEY", "")
    if not api_key:
        return ""

    query = gym_name
    if city:
        query += f" {city}"
    if state:
        query += f" {state}"

    try:
        resp = _SESSION.get(SERPAPI_URL, params={
            "q": query,
            "engine": "google",
            "api_key": api_key,
            "num": 5,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return ""

    for result in data.get("organic_results", []):
        link = result.get("link", "")
        if not link:
            continue
        if _is_skip_url(link):
            continue
        return link

    return ""


def resolve_website(lead_url: str, gym_name: str = "",
                    city: str = "", state: str = "") -> str:
    """Resolve a platform URL to the real gym website.

    Strategy:
      1. Try fetching the platform page and extracting outbound links
      2. If that fails (JS-rendered like MindBody), SerpAPI search

    Returns the resolved URL, or the original URL if resolution fails.
    """
    if not lead_url:
        return lead_url

    if not _is_platform_url(lead_url):
        return lead_url

    # Strategy 1: Extract links from platform page directly
    # Skip for domains known to have junk outbound links (JS-rendered or ad-heavy)
    netloc = urlparse(lead_url).netloc.lower()
    skip_platform_fetch = any(d in netloc for d in ("mindbodyonline.com", "f45training.com"))
    if not skip_platform_fetch:
        resolved = _resolve_from_platform(lead_url)
        if resolved:
            print(f"    [resolve] {lead_url} -> {resolved} (platform link)")
            return resolved

    # Strategy 2: Search for the gym's real website
    # Try Serper.dev first (free tier), then SerpAPI as fallback
    if gym_name:
        for search_fn, label in [(_search_serper, "serper"), (_search_serpapi, "serpapi")]:
            resolved = search_fn(gym_name, city, state)
            if resolved:
                print(f"    [resolve] {lead_url} -> {resolved} ({label})")
                return resolved

    return lead_url

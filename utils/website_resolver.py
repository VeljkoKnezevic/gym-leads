"""Resolve platform URLs (MindBody, CrossFit, F45, etc.) to real gym websites."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT})

# Domains that are platform/aggregator URLs (not real gym websites)
PLATFORM_DOMAINS = {
    "mindbodyonline.com", "www.mindbodyonline.com",
    "clients.mindbodyonline.com",
    "crossfit.com", "www.crossfit.com", "map.crossfit.com",
    "f45training.com", "www.f45training.com",
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
}


def _is_platform_url(url: str) -> bool:
    """Check if a URL is a known platform/aggregator URL."""
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc == d or netloc.endswith("." + d) for d in PLATFORM_DOMAINS)
    except Exception:
        return False


def _is_noise_url(url: str) -> bool:
    """Check if a URL is social media, aggregator, or other noise."""
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc == d or netloc.endswith("." + d)
                   for d in (NOISE_DOMAINS | PLATFORM_DOMAINS))
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

        # Must be absolute URL with http(s)
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue

        netloc = parsed.netloc.lower()

        # Skip same-domain links
        if netloc == source_netloc or netloc.endswith("." + source_netloc):
            continue

        # Skip noise domains
        if _is_noise_url(href):
            continue

        # Normalize
        clean = parsed._replace(fragment="").geturl()
        if clean in seen:
            continue
        seen.add(clean)

        # Prioritize links with "website" or "visit" in anchor text
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


def resolve_website(lead_url: str, gym_name: str = "") -> str:
    """Resolve a platform URL to the real gym website.

    Returns the resolved URL, or the original URL if resolution fails or isn't needed.
    """
    if not lead_url:
        return lead_url

    if not _is_platform_url(lead_url):
        return lead_url

    resolved = _resolve_from_platform(lead_url)
    if resolved:
        print(f"    [resolve] {lead_url} -> {resolved}")
        return resolved

    return lead_url

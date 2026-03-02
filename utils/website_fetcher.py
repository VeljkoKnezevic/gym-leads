"""Fetch and extract readable text from a gym website (no browser required)."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

PRIORITY_KEYWORDS = ["about", "team", "owner", "founder", "story", "contact"]

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT})


def _get_text(html: str) -> str:
    """Strip tags from HTML and return plain text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())


def _internal_links(html: str, base_url: str) -> list[str]:
    """Return all internal links found on the page, ranked by priority keywords."""
    soup = BeautifulSoup(html, "html.parser")
    base_netloc = urlparse(base_url).netloc

    seen: set[str] = set()
    priority: list[str] = []
    rest: list[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(base_url, href)
        parsed = urlparse(full)

        # Keep only same-domain http(s) links
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc != base_netloc:
            continue

        # Normalise: drop fragment and query
        clean = parsed._replace(fragment="", query="").geturl()
        if clean in seen or clean == base_url:
            continue
        seen.add(clean)

        text = (a.get_text() + " " + href).lower()
        if any(kw in text for kw in PRIORITY_KEYWORDS):
            priority.append(clean)
        else:
            rest.append(clean)

    return priority + rest


def fetch_website_text(
    url: str,
    max_pages: int = 5,
    max_chars: int = 8000,
) -> str:
    """Return concatenated plain text from the homepage and key sub-pages.

    Visits up to *max_pages* pages, prioritising about/team/contact pages.
    Returns empty string on any failure — never raises.
    """
    if not url:
        return ""

    try:
        resp = _SESSION.get(url, timeout=10, allow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return ""

    homepage_html = resp.text
    homepage_url = resp.url  # may differ from original after redirects
    chunks: list[str] = [_get_text(homepage_html)]

    links = _internal_links(homepage_html, homepage_url)

    for link in links[: max_pages - 1]:
        try:
            r = _SESSION.get(link, timeout=10, allow_redirects=True)
            r.raise_for_status()
            chunks.append(_get_text(r.text))
        except Exception:
            continue

    combined = " ".join(chunks)
    return combined[:max_chars]

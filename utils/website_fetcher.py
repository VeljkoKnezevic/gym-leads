"""Fetch and extract readable text from a gym website (no browser required)."""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

PRIORITY_KEYWORDS = [
    "about", "team", "owner", "founder", "story", "contact",
    "leadership", "staff", "coaches", "our-story", "our-team",
    "who-we-are", "meet", "bio", "history", "mission",
]

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT})


def _extract_structured_data(soup: BeautifulSoup) -> str:
    """Extract owner/person info from JSON-LD, meta author, and OpenGraph tags."""
    parts: list[str] = []

    # JSON-LD structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            _extract_jsonld_names(data, parts)
        except (json.JSONDecodeError, TypeError):
            continue

    # Meta author tag
    author_meta = soup.find("meta", attrs={"name": "author"})
    if author_meta:
        content = author_meta.get("content", "")
        if content:
            parts.append(f"Meta Author: {content}")

    # OpenGraph tags
    for prop in ("og:site_name", "og:author", "article:author"):
        og = soup.find("meta", attrs={"property": prop})
        if og:
            content = og.get("content", "")
            if content:
                parts.append(f"OG {prop}: {content}")

    return " | ".join(parts) if parts else ""


def _extract_jsonld_names(data, parts: list[str]) -> None:
    """Recursively extract person/organization names from JSON-LD data."""
    if isinstance(data, list):
        for item in data:
            _extract_jsonld_names(item, parts)
        return

    if not isinstance(data, dict):
        return

    schema_type = data.get("@type", "")
    if isinstance(schema_type, list):
        schema_type = " ".join(schema_type)

    # Extract names from Person, Organization, LocalBusiness types
    for type_kw in ("Person", "Organization", "LocalBusiness", "HealthClub",
                    "SportsActivityLocation", "ExerciseGym"):
        if type_kw.lower() in schema_type.lower():
            name = data.get("name", "")
            if name:
                parts.append(f"Schema {type_kw}: {name}")
            founder = data.get("founder")
            if founder:
                if isinstance(founder, dict):
                    fname = founder.get("name", "")
                    if fname:
                        parts.append(f"Schema Founder: {fname}")
                elif isinstance(founder, str):
                    parts.append(f"Schema Founder: {founder}")
            # Check for employee/member fields
            for field in ("employee", "member", "owns"):
                val = data.get(field)
                if isinstance(val, dict) and val.get("name"):
                    parts.append(f"Schema {field}: {val['name']}")
                elif isinstance(val, list):
                    for v in val[:3]:  # limit
                        if isinstance(v, dict) and v.get("name"):
                            parts.append(f"Schema {field}: {v['name']}")

    # Recurse into nested objects
    for key, val in data.items():
        if isinstance(val, (dict, list)):
            _extract_jsonld_names(val, parts)


def _get_text(html: str, is_subpage: bool = False) -> str:
    """Strip tags from HTML and return plain text.

    For subpages (about/team), keep header/footer since small gym sites
    put owner info there.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    if not is_subpage:
        for tag in soup(["header", "footer", "nav"]):
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


_IG_SKIP_SLUGS = {
    "explore", "p", "stories", "reels", "reel", "tv", "accounts", "directory",
    "developer", "legal", "privacy", "about", "press", "api", "blog", "help",
    "sharedfiles", "graphql", "static",
}

# Platform / SaaS slugs — these are the website builder's own page, not the gym's
_PLATFORM_SLUGS = {
    "wix", "wixsite", "mindbody", "mindbodyonline", "mindbodybusiness",
    "squarespace", "godaddy", "wordpress", "weebly", "shopify",
    "zenplanner", "glofox", "clubready", "wodify", "pushpress",
    "gymdesk", "pike13", "marianaiframes",
}


def fetch_raw_html(url: str) -> str:
    """Fetch a URL and return raw HTML. Returns empty string on any error."""
    if not url:
        return ""
    try:
        resp = _SESSION.get(url, timeout=10, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return ""


def extract_facebook_url_from_html(html: str) -> str:
    """Extract Facebook page URL from raw HTML string."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "facebook.com" not in href:
            continue
        if any(skip in href for skip in ("/sharer", "/dialog", "/share?", "facebook.com/ad")):
            continue
        parsed = urlparse(href)
        path = parsed.path.strip("/")
        if not path or path in ("", "pages", "groups"):
            continue
        # profile.php?id=XXXXX — keep the id param, it IS the page identifier
        if path == "profile.php":
            from urllib.parse import parse_qs
            pid = parse_qs(parsed.query).get("id", [None])[0]
            if pid:
                return f"https://www.facebook.com/profile.php?id={pid}"
            continue  # profile.php with no id is useless
        # Skip platform/SaaS pages (e.g. facebook.com/wix, facebook.com/mindbodybusiness)
        top_slug = path.split("/")[0].lower()
        if top_slug in _PLATFORM_SLUGS:
            continue
        return parsed._replace(scheme="https", fragment="", query="").geturl()
    return ""


def extract_instagram_url_from_html(html: str) -> str:
    """Extract Instagram page URL from raw HTML string."""
    if not html:
        return ""
    matches = re.findall(
        r'https?://(?:www\.)?instagram\.com/([\w.\-]+)(?:[/?"\'\s]|$)',
        html,
        re.IGNORECASE,
    )
    for slug in matches:
        clean = slug.rstrip("./").lower()
        if clean not in _IG_SKIP_SLUGS and clean not in _PLATFORM_SLUGS and len(clean) >= 3:
            return f"https://www.instagram.com/{slug}"
    return ""


def extract_facebook_url(url: str) -> str:
    """Fetch a gym's website and extract their Facebook page URL from it.

    Looks for links to facebook.com that point to a page (not share buttons, etc.).
    Returns the Facebook URL or empty string. Never raises.
    """
    return extract_facebook_url_from_html(fetch_raw_html(url))


def fetch_website_text(
    url: str,
    max_pages: int = 5,
    max_chars: int = 15000,
) -> str:
    """Return concatenated plain text from the homepage and key sub-pages.

    Visits up to *max_pages* pages, prioritising about/team/contact pages.
    Includes structured data (JSON-LD, meta tags) and labels each section.
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

    # Extract structured data from homepage
    soup = BeautifulSoup(homepage_html, "html.parser")
    structured = _extract_structured_data(soup)

    chunks: list[str] = []

    # Add structured data first if found
    if structured:
        chunks.append(f"[STRUCTURED DATA] {structured}")

    # Homepage text
    chunks.append(f"[HOMEPAGE] {_get_text(homepage_html, is_subpage=False)}")

    links = _internal_links(homepage_html, homepage_url)

    for link in links[: max_pages - 1]:
        try:
            r = _SESSION.get(link, timeout=10, allow_redirects=True)
            r.raise_for_status()
            label = link.split("/")[-1] or "page"
            chunks.append(f"[ABOUT/TEAM PAGE: {label}] {_get_text(r.text, is_subpage=True)}")
        except Exception:
            continue

    combined = " ".join(chunks)
    return combined[:max_chars]

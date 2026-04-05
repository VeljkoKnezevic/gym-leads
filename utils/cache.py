"""Website cache utilities for gym lead enrichment."""

import re
from pathlib import Path

from scrapers.base import Lead

DEFAULT_CACHE_DIR = "output/website_cache"

_FB_SKIP_SLUGS = {
    "sharer", "share", "plugins", "login", "dialog", "tr", "photo", "video",
    "events", "groups", "pages", "ads", "help", "policy", "legal", "about",
    "watch", "marketplace", "gaming", "profile.php", "hashtag", "permalink",
    "home", "story.php", "reel", "reels",
}


def gym_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60]


def city_slug(city: str, state: str) -> str:
    combined = f"{city}-{state}".lower()
    return re.sub(r"[^a-z0-9]+", "-", combined).strip("-")


def get_cache_path(lead: Lead, cache_dir: str = DEFAULT_CACHE_DIR) -> Path:
    folder = Path(cache_dir) / city_slug(lead.city, lead.state)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{gym_slug(lead.name)}.txt"


def extract_fb_url_from_content(content: str) -> str:
    """Find a Facebook page URL within cached plain-text content (fallback)."""
    matches = re.findall(
        r'https?://(?:www\.)?facebook\.com/([\w.\-]+)(?:[/?"\'\s]|$)',
        content, re.IGNORECASE,
    )
    for slug in matches:
        clean = slug.rstrip("./").lower()
        if clean not in _FB_SKIP_SLUGS and len(clean) >= 3:
            return f"https://www.facebook.com/{slug}"
    return ""

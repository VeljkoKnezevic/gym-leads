"""Name-similarity deduplication across sources."""

import re
from difflib import SequenceMatcher

from scrapers.base import Lead
from urllib.parse import urlparse

_PLATFORM_HOSTS = {
    "mindbodyonline.com", "crossfit.com", "map.crossfit.com",
    "f45training.com", "wodify.com", "zenplanner.com",
}


def _is_platform_url(url: str) -> bool:
    if not url:
        return False
    try:
        netloc = urlparse(url).netloc.lower().lstrip("www.")
        return any(netloc == h or netloc.endswith("." + h) for h in _PLATFORM_HOSTS)
    except Exception:
        return False


# US state abbreviation <-> full name mapping for normalization
_STATE_ABBREV = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia",
}
_STATE_TO_ABBREV = {v: k.lower() for k, v in _STATE_ABBREV.items()}


def _normalize_state(state: str) -> str:
    """Normalize state to lowercase abbreviation for consistent comparison."""
    s = state.strip().lower()
    if s.upper() in _STATE_ABBREV:
        return s.lower()
    return _STATE_TO_ABBREV.get(s, s)


def _normalize(name: str) -> str:
    """Normalize a gym name for comparison."""
    name = name.lower().strip()
    # Remove common suffixes/prefixes that don't help matching
    # Includes brand names (crossfit, hyrox, f45, orangetheory) to catch cross-source dupes
    for word in ("llc", "inc", "the", "gym", "fitness", "studio", "center", "centre",
                 "crossfit", "hyrox", "f45", "orangetheory", "training"):
        name = re.sub(rf"\b{word}\b", "", name)
    # Remove trailing location codes like "#0196", "DC.MD.VA", "EM-VA-20005"
    name = re.sub(r"#\w+", "", name)
    name = re.sub(r"\b[A-Z]{2}[\.\-][A-Z]{2}[\.\-\w]*", "", name, flags=re.IGNORECASE)
    # Collapse whitespace and strip punctuation
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _is_name_match(a: str, b: str, threshold: float) -> bool:
    """Check if two normalized names match via similarity or containment."""
    if not a or not b:
        return False
    # Exact match
    if a == b:
        return True
    # Standard similarity
    if SequenceMatcher(None, a, b).ratio() >= threshold:
        return True
    # Containment: shorter name is a prefix/subset of longer name
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 4 and long.startswith(short):
        return True
    return False


def _merge_leads(existing: Lead, new: Lead) -> Lead:
    """Merge two leads, preferring non-empty fields and combining sources."""
    # Prefer the real gym website over a platform URL (mindbody, crossfit.com, etc.)
    existing_web = existing.website or ""
    new_web = new.website or ""
    if _is_platform_url(existing_web) and new_web and not _is_platform_url(new_web):
        best_website = new_web
    else:
        best_website = existing_web or new_web

    merged = Lead(
        name=existing.name or new.name,
        address=existing.address or new.address,
        city=existing.city or new.city,
        state=existing.state or new.state,
        phone=existing.phone or new.phone,
        website=best_website,
        type=existing.type or new.type,
        source=existing.source,
        owner=existing.owner or new.owner,
        owner_confidence=existing.owner_confidence or new.owner_confidence,
        facebook_url=existing.facebook_url or new.facebook_url,
        ad_pixels=existing.ad_pixels or new.ad_pixels,
    )
    # Combine sources (e.g., "mindbody, crossfit")
    existing_sources = set(s.strip() for s in existing.source.split(","))
    new_sources = set(s.strip() for s in new.source.split(","))
    all_sources = sorted(existing_sources | new_sources)
    merged.source = ", ".join(all_sources)
    return merged


# Corporate/franchise chains to exclude — can't sell to these yet
# Only truly corporate-owned chains (no local franchise owner to contact).
# Franchise brands like OTF, Gold's, F45, etc. are kept — they have local owners.
_CORPORATE_NAMES = [
    "equinox", "soulcycle", "la fitness", "life time fitness",
    "24 hour fitness", "planet fitness", "chuze fitness",
    "eos fitness", "in-shape health clubs",
    "ymca", "ywca", "jcc", "jewish community center",
    "hospital fitness", "university recreation", "municipal recreation",
]


def _is_corporate(name: str) -> bool:
    """Check if a gym name matches a corporate/franchise chain."""
    n = re.sub(r"[^a-z0-9\s]", "", name.lower().strip())
    n = re.sub(r"\s+", " ", n).strip()
    for corp in _CORPORATE_NAMES:
        if corp in n or n in corp:
            return True
    return False


def filter_corporate(leads: list[Lead]) -> list[Lead]:
    """Remove leads that match corporate/franchise chains."""
    kept = []
    removed = 0
    for lead in leads:
        if _is_corporate(lead.name):
            removed += 1
        else:
            kept.append(lead)
    if removed:
        print(f"  [filter] Removed {removed} corporate/franchise leads")
    return kept


def deduplicate(leads: list[Lead], threshold: float = 0.85) -> list[Lead]:
    """Remove duplicate leads across sources using name similarity + same city/state.

    Two leads are considered duplicates if:
    - Normalized name similarity > threshold (default 85%)
    - Same city (case-insensitive) AND same state (abbrev-normalized)
    """
    if not leads:
        return []

    unique: list[Lead] = []

    for lead in leads:
        norm_name = _normalize(lead.name)
        lead_city = (lead.city or "").lower().strip()
        lead_state = _normalize_state(lead.state or "")

        matched = False
        for i, existing in enumerate(unique):
            existing_norm = _normalize(existing.name)
            existing_city = (existing.city or "").lower().strip()
            existing_state = _normalize_state(existing.state or "")

            # Must be same city/state to be a duplicate
            if lead_city != existing_city or lead_state != existing_state:
                continue

            if _is_name_match(norm_name, existing_norm, threshold):
                unique[i] = _merge_leads(existing, lead)
                matched = True
                break

        if not matched:
            unique.append(lead)

    return unique

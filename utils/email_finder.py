"""Extract owner-level emails from gym website HTML.

Filters out generic inboxes (info@, contact@, etc.) and prefers emails
that match the owner's name when one is known.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Generic email prefixes that are NOT personal/owner emails
GENERIC_PREFIXES = {
    "info", "help", "support", "contact", "hello", "admin", "sales", "mail",
    "office", "service", "booking", "reservations", "team", "enquiries",
    "enquiry", "questions", "noreply", "no-reply", "webmaster", "postmaster",
    "accounts", "billing", "media", "press", "pr", "careers", "jobs", "hr",
    "hiring", "events", "membership", "memberships", "classes", "schedule",
    "studio", "gym", "fitness", "front", "frontdesk", "desk", "reception",
    "customerservice", "cs", "general", "marketing", "inbox", "newsletter",
    "unsubscribe", "donotreply", "do-not-reply", "privacy", "legal", "abuse",
    "spam", "security", "feedback", "hello", "hi", "hey", "welcome",
}

# Domains that are framework/platform noise, not real gym emails
_NOISE_DOMAINS = {
    "sentry.io", "sentry-next.io", "wixpress.com", "squarespace.com",
    "wordpress.com", "godaddy.com", "cloudflare.com", "amazonaws.com",
    "googletagmanager.com", "google.com", "facebook.com", "instagram.com",
    "example.com", "domain.com", "email.com", "test.com", "placeholder.com",
    "mailchimp.com", "constantcontact.com", "hubspot.com",
}

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.ASCII,
)

_MAILTO_RE = re.compile(
    r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})',
    re.IGNORECASE,
)


# Substrings that indicate a generic/business inbox even when embedded in the prefix
_GENERIC_SUBSTRINGS = {
    "contactus", "contact-us", "noreply", "no-reply", "donotreply",
    "do-not-reply", "frontdesk", "front-desk", "customerservice",
    "unsubscribe",
}


def _is_generic(prefix: str) -> bool:
    p = prefix.lower()
    if p in GENERIC_PREFIXES:
        return True
    return any(sub in p for sub in _GENERIC_SUBSTRINGS)


def _is_noise_domain(domain: str) -> bool:
    domain = domain.lower()
    return any(domain == nd or domain.endswith("." + nd) for nd in _NOISE_DOMAINS)


def _score_email(prefix: str, owner_name: str) -> int:
    """Score an email prefix against the owner's name. Higher = better match."""
    if not owner_name:
        return 0
    parts = owner_name.lower().split()
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) > 1 else ""
    prefix_lower = prefix.lower()
    score = 0
    if first and first in prefix_lower:
        score += 2
    if last and last in prefix_lower:
        score += 1
    return score


def find_email(html: str, owner_name: str = "") -> str:
    """Return the best non-generic email found in the HTML.

    Priority:
    1. mailto: links (most explicit — the site put it there on purpose)
    2. Any email found via regex in the full HTML

    Within each group, emails matching the owner's name score higher.
    Generic prefixes (info@, contact@, etc.) are excluded entirely.
    Returns empty string if nothing suitable is found.
    """
    if not html:
        return ""

    candidates: list[tuple[int, str]] = []  # (score, email)
    seen: set[str] = set()

    def _add(email: str, bonus: int = 0) -> None:
        email = email.strip().lower()
        if email in seen:
            return
        seen.add(email)
        try:
            prefix, domain = email.rsplit("@", 1)
        except ValueError:
            return
        if _is_generic(prefix) or _is_noise_domain(domain):
            return
        # Skip placeholder/example emails
        if prefix in ("user", "name", "email", "your", "youremail",
                       "your-email", "username", "test", "example"):
            return
        score = _score_email(prefix, owner_name) + bonus
        candidates.append((score, email))

    # Pass 1: mailto: links (bonus +3 for being explicit)
    for match in _MAILTO_RE.finditer(html):
        _add(match.group(1), bonus=3)

    # Pass 2: any email pattern in HTML
    for match in _EMAIL_RE.finditer(html):
        _add(match.group(0))

    if not candidates:
        return ""

    # Return the highest-scoring email (stable sort keeps insertion order for ties)
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

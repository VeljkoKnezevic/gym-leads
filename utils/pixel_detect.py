"""Detect ad tracking pixels in website HTML.

Checks for Meta Pixel, Google Ads/GTM, and TikTok Pixel.
Fast, local, no browser needed — just raw HTML string matching.
"""

from __future__ import annotations


# Meta / Facebook Pixel signatures
_META_SIGS = [
    "fbq(",
    "facebook.com/tr?",
    "fbevents.js",
    "connect.facebook.net",
]

# Google Ads / Tag Manager signatures
_GOOGLE_ADS_SIGS = [
    "googleadservices.com",
    "google_conversion",
    "googleads.g.doubleclick.net",
]

_GTM_SIGS = [
    "googletagmanager.com/gtm.js",
    "GTM-",
]

# TikTok Pixel signatures
_TIKTOK_SIGS = [
    "analytics.tiktok.com",
    "tiktok.com/i18n/pixel",
]


def detect_pixels(html: str) -> dict[str, bool]:
    """Return which ad pixels are present in the HTML.

    Returns dict with keys: meta_pixel, google_ads, tiktok_pixel
    GTM is intentionally excluded — it's a tag container typically used for
    analytics, not a signal that the business is running ads.
    """
    html_lower = html.lower()
    return {
        "meta_pixel": any(sig.lower() in html_lower for sig in _META_SIGS),
        "google_ads": any(sig.lower() in html_lower for sig in _GOOGLE_ADS_SIGS),
        "tiktok_pixel": any(sig in html for sig in _TIKTOK_SIGS)
                        or ("tiktok" in html_lower and "pixel" in html_lower),
    }


def has_any_ad_pixel(html: str) -> bool:
    """Quick check: does this HTML contain ANY ad tracking pixel?"""
    pixels = detect_pixels(html)
    return any(pixels.values())


def pixel_summary(pixels: dict[str, bool]) -> str:
    """Return a short string like 'meta,gtm,tiktok' for detected pixels."""
    names = []
    if pixels.get("meta_pixel"):
        names.append("meta")
    if pixels.get("google_ads"):
        names.append("google")
    if pixels.get("tiktok_pixel"):
        names.append("tiktok")
    return ",".join(names) if names else ""

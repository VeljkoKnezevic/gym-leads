"""Pre-fetch gym websites and store content locally.

Fetches each gym's website once after scraping, caches the text content per gym,
and extracts:
  - Facebook page URL
  - Instagram page URL
  - Owner-level email (non-generic)

Strategy: requests first (fast), Playwright fallback for JS-heavy sites.

Usage:
    python prefetch.py --input output/norwalk-ct-leads.csv
    python prefetch.py --input output/norwalk-ct-leads.csv --workers 4
"""

from __future__ import annotations

import argparse
import atexit
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scrapers.base import Lead
from utils.csv_writer import read_leads_csv, write_leads_csv
from utils.dedup import filter_non_fitness
from utils.website_fetcher import (
    fetch_website_text,
    fetch_raw_html,
    extract_facebook_url_from_html,
    extract_instagram_url_from_html,
    _internal_links,
    _get_text,
    _extract_structured_data,
)
from utils.cache import get_cache_path, DEFAULT_CACHE_DIR

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

_thread_local = threading.local()
_pw_instances: list = []  # track (pw, browser) for cleanup
_pw_lock = threading.Lock()


def _get_pw_browser():
    """Get (or create) a thread-local Playwright browser instance."""
    if not hasattr(_thread_local, "browser"):
        from playwright.sync_api import sync_playwright
        _thread_local.pw = sync_playwright().__enter__()
        _thread_local.browser = _thread_local.pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        with _pw_lock:
            _pw_instances.append((_thread_local.pw, _thread_local.browser))
    return _thread_local.browser


def _cleanup_pw():
    """Close all Playwright browser instances."""
    for pw, browser in _pw_instances:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.__exit__(None, None, None)
        except Exception:
            pass
    _pw_instances.clear()


atexit.register(_cleanup_pw)


def _render_with_playwright(url: str) -> str:
    """Render a page with Playwright and return the full HTML."""
    try:
        browser = _get_pw_browser()
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()
        page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "font", "media")
            else route.continue_(),
        )
        page.goto(url, timeout=15000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        html = page.content()
        context.close()
        return html
    except Exception:
        return ""


def _prefetch_lead(lead: Lead, cache_dir: str) -> tuple[Lead, str]:
    """Fetch website, cache text, extract FB URL / Instagram URL / email."""
    if not lead.website:
        return lead, "no_website"

    cache_file = get_cache_path(lead, cache_dir)
    already_cached = cache_file.exists() and cache_file.stat().st_size > 100

    # --- Step 1: Fetch homepage + subpages (about/contact/team) via requests ---
    all_html_parts: list[str] = []
    raw_html = fetch_raw_html(lead.website)

    if raw_html:
        all_html_parts.append(raw_html)
        # Also fetch key subpages (contact, about, team) where email/socials often live
        subpage_links = _internal_links(raw_html, lead.website)
        for link in subpage_links[:4]:
            sub_html = fetch_raw_html(link)
            if sub_html:
                all_html_parts.append(sub_html)

    # Extract FB/IG from ALL fetched pages
    for html_part in all_html_parts:
        if not lead.facebook_url:
            lead.facebook_url = extract_facebook_url_from_html(html_part)
        if not lead.instagram_url:
            lead.instagram_url = extract_instagram_url_from_html(html_part)

    # --- Step 2: Playwright fallback if social links still missing ---
    needs_pw = (not lead.facebook_url or not lead.instagram_url)
    pw_used = False

    if needs_pw:
        rendered_html = _render_with_playwright(lead.website)
        if rendered_html:
            pw_used = True
            all_html_parts.append(rendered_html)
            if not lead.facebook_url:
                lead.facebook_url = extract_facebook_url_from_html(rendered_html)
            if not lead.instagram_url:
                lead.instagram_url = extract_instagram_url_from_html(rendered_html)

    # --- Step 3: Cache text content (reuse HTML already fetched in Step 1) ---
    if already_cached:
        return lead, "cached+pw" if pw_used else "cached"

    if all_html_parts:
        from bs4 import BeautifulSoup
        chunks: list[str] = []
        # Structured data from homepage
        soup = BeautifulSoup(all_html_parts[0], "html.parser")
        structured = _extract_structured_data(soup)
        if structured:
            chunks.append(f"[STRUCTURED DATA] {structured}")
        # Homepage text
        chunks.append(f"[HOMEPAGE] {_get_text(all_html_parts[0], is_subpage=False)}")
        # Subpage text
        for html_part in all_html_parts[1:]:
            chunks.append(f"[SUBPAGE] {_get_text(html_part, is_subpage=True)}")
        content = " ".join(chunks)[:20000]
    else:
        content = ""

    if content.strip():
        cache_file.write_text(content, encoding="utf-8")
        status = "fetched+pw" if pw_used else "fetched"
    else:
        status = "empty"

    return lead, status




def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-fetch gym websites and cache locally")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="",
                        help="Output CSV path (default: <input-stem>-prefetched.csv)")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                        help=f"Directory to store cached website content (default: {DEFAULT_CACHE_DIR})")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel workers (default: 4)")
    args = parser.parse_args()

    if args.output:
        output_path = args.output
    else:
        inp = Path(args.input)
        output_path = str(inp.parent / f"{inp.stem}-prefetched.csv")

    leads = filter_non_fitness(read_leads_csv(args.input))

    with_website = sum(1 for l in leads if l.website)
    print(f"[prefetch] {len(leads)} leads — {with_website} with website, "
          f"{len(leads) - with_website} without")
    print(f"[prefetch] Cache dir: {args.cache_dir}")
    print(f"[prefetch] Strategy: requests first, Playwright fallback for JS-heavy sites")

    stats: dict[str, int] = {}
    original_state = {id(l): (l.facebook_url, l.instagram_url) for l in leads}

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_prefetch_lead, lead, args.cache_dir): lead
            for lead in leads
        }
        for i, future in enumerate(as_completed(futures), 1):
            orig_lead = futures[future]
            try:
                updated_lead, status = future.result()
                stats[status] = stats.get(status, 0) + 1
                orig_fb, orig_ig = original_state[id(orig_lead)]
                tags = []
                if updated_lead.facebook_url and not orig_fb:
                    tags.append("FB")
                if updated_lead.instagram_url and not orig_ig:
                    tags.append("IG")
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                print(f"[prefetch] [{i}/{len(leads)}] {updated_lead.name!r} -> {status}{tag_str}")
            except Exception as e:
                print(f"[prefetch] ERROR on '{orig_lead.name}': {e}")
                stats["error"] = stats.get("error", 0) + 1

    write_leads_csv(leads, output_path)

    total_fb = sum(1 for l in leads if l.facebook_url)
    total_ig = sum(1 for l in leads if l.instagram_url)
    pct = lambda n: f"{100 * n // len(leads)}%" if leads else "0%"

    print(f"\n[prefetch] === Summary ===")
    for k, v in sorted(stats.items()):
        print(f"[prefetch]   {k}: {v}")
    print(f"[prefetch] Facebook URLs:  {total_fb}/{len(leads)} ({pct(total_fb)})")
    print(f"[prefetch] Instagram URLs: {total_ig}/{len(leads)} ({pct(total_ig)})")
    print(f"[prefetch] Output CSV: {output_path}")


if __name__ == "__main__":
    main()

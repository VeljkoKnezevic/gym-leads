"""CLI entry point for gym lead scraper."""

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Load .env if present
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from utils.geo import geocode_city
from utils.dedup import deduplicate
from utils.csv_writer import write_leads_csv
from scrapers import MindBodyScraper, CrossFitScraper, SerpApiScraper

SCRAPER_MAP = {
    "mindbody": MindBodyScraper,
    "crossfit": CrossFitScraper,
    "google_maps": SerpApiScraper,
}

ALL_SOURCES = list(SCRAPER_MAP.keys())


def run_scraper(source: str, scraper_cls, geo: dict, headless: bool, enrich: bool = True):
    """Run a single scraper and return (source, leads, elapsed_seconds)."""
    scraper = scraper_cls(geo, headless=headless, enrich=enrich)
    start = time.time()
    leads = scraper.run()
    return source, leads, time.time() - start


def main():
    parser = argparse.ArgumentParser(
        description="Scrape gym/fitness facility leads for cold calling."
    )
    parser.add_argument(
        "--city",
        required=True,
        help='City to search, e.g. "Ashburn, VA" or "Denver, CO"',
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=ALL_SOURCES,
        default=ALL_SOURCES,
        help=f"Sources to scrape (default: all). Choices: {', '.join(ALL_SOURCES)}",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: output/<city-slug>-leads.csv)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode (visible) for debugging",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Run scrapers sequentially instead of in parallel (lower memory use)",
    )
    args = parser.parse_args()

    # Geocode the city
    print(f"Geocoding: {args.city}")
    try:
        geo = geocode_city(args.city)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  -> {geo['city']}, {geo['state']} ({geo['lat']:.4f}, {geo['lng']:.4f})")

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", args.city.lower()).strip("-")
        output_path = os.path.join("output", f"{slug}-leads.csv")

    headless = not args.headed

    # Run all scrapers in parallel
    all_leads = []
    source_results: dict[str, tuple[list, float]] = {}  # source -> (leads, elapsed)

    workers = 1 if args.sequential else len(args.sources)
    print(f"\nRunning {len(args.sources)} scraper(s) {'sequentially' if args.sequential else 'in parallel'}...")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_scraper, source, SCRAPER_MAP[source], geo, headless, True): source
            for source in args.sources
        }
        for future in as_completed(futures):
            source, leads, elapsed = future.result()
            source_results[source] = (leads, elapsed)
            all_leads.extend(leads)

    if not all_leads:
        print("\nNo leads found from any source.")
        sys.exit(0)

    # Deduplicate across sources
    unique_leads = deduplicate(all_leads)

    # Write CSV
    path = write_leads_csv(unique_leads, output_path)

    # Summary table
    city_label = f"{geo['city']}, {geo['state']}"
    print(f"\n=== Results: {city_label} ===")
    for source in args.sources:
        if source not in source_results:
            continue
        leads, elapsed = source_results[source]
        with_phone = sum(1 for l in leads if l.phone)
        print(f"  {source:<12} {len(leads):>4} leads   ({with_phone} with phone)   {elapsed:.0f}s")
    print(f"  {'-' * 47}")
    total_with_phone = sum(1 for l in unique_leads if l.phone)
    print(f"  {'Total':<12} {len(unique_leads):>4} unique  ({total_with_phone} with phone)")
    print(f"  Output: {path}")


if __name__ == "__main__":
    main()

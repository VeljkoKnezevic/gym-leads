"""Filter gym leads to only those actively running Meta ads.

Usage:
    python ad_check.py --input output/ashburn-va-leads.csv
    python ad_check.py --input output/ashburn-va-leads.csv --min-ads 2
"""

from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Windows terminals default to cp1252 which chokes on non-ASCII gym names
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scrapers.base import Lead, CSV_COLUMNS
from utils.csv_writer import write_leads_csv
from utils.dedup import filter_corporate
from utils.meta_ads import check_meta_ads


def _read_leads_csv(path: str) -> list[Lead]:
    """Read a CSV into Lead objects."""
    leads: list[Lead] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads.append(Lead(
                name=row.get("name", ""),
                address=row.get("address", ""),
                city=row.get("city", ""),
                state=row.get("state", ""),
                phone=row.get("phone", ""),
                website=row.get("website", ""),
                type=row.get("type", ""),
                source=row.get("source", ""),
                owner=row.get("owner", ""),
                owner_confidence=row.get("owner_confidence", ""),
                meta_ads_count=row.get("meta_ads_count", ""),
            ))
    return leads


def _check_lead(lead: Lead) -> Lead:
    """Check a single lead for active Meta ads."""
    meta_count = check_meta_ads(lead.name)
    lead.meta_ads_count = str(meta_count)
    return lead


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter gym leads to active advertisers")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="", help="Output CSV path (default: input stem + '-ads.csv')")
    parser.add_argument("--workers", type=int, default=3, help="Parallel workers (default: 3)")
    parser.add_argument("--min-ads", type=int, default=1, help="Minimum active ads to keep a lead (default: 1)")
    args = parser.parse_args()

    # Default output: input stem + '-ads.csv'
    if args.output:
        output_path = args.output
    else:
        stem = Path(args.input).stem
        parent = Path(args.input).parent
        output_path = str(parent / f"{stem}-ads.csv")

    leads = _read_leads_csv(args.input)
    leads = filter_corporate(leads)

    to_check: list[Lead] = []
    already_checked: list[Lead] = []

    for lead in leads:
        if lead.meta_ads_count:
            already_checked.append(lead)
        else:
            to_check.append(lead)

    print(f"[ad_check] {len(leads)} total leads — {len(to_check)} to check, "
          f"{len(already_checked)} already checked")

    # Check ads (sequential — each check launches its own browser)
    for i, lead in enumerate(to_check, 1):
        try:
            _check_lead(lead)
        except Exception as e:
            print(f"[ad_check] ERROR checking '{lead.name}': {e}")
            lead.meta_ads_count = "0"

        print(f"[ad_check] [{i}/{len(to_check)}] {lead.name!r} -> meta={lead.meta_ads_count}")

    # Filter: keep leads with enough ad signals
    all_leads = already_checked + to_check
    passed = []
    failed = 0
    for lead in all_leads:
        total = int(lead.meta_ads_count or 0)
        if total >= args.min_ads:
            passed.append(lead)
        else:
            failed += 1

    write_leads_csv(passed, output_path)

    print(f"\n[ad_check] Done — {len(passed)} leads with active ads, {failed} filtered out")
    print(f"[ad_check] Wrote {output_path}")


if __name__ == "__main__":
    main()

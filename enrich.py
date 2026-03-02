"""Enrich gym lead CSVs with owner names via a local Ollama model.

Usage:
    python enrich.py --input output/ashburn-va-leads.csv
    python enrich.py --input leads.csv --output leads-enriched.csv --workers 3 --model mistral:7b
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# Windows terminals default to cp1252 which chokes on non-ASCII gym names
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scrapers.base import Lead, CSV_COLUMNS
from utils.csv_writer import write_leads_csv
from utils.website_fetcher import fetch_website_text
from utils.ollama_client import find_owner


def _read_leads_csv(path: str) -> list[Lead]:
    """Read a CSV into Lead objects. Tolerates CSVs without an 'owner' column."""
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
            ))
    return leads


def _enrich_lead(lead: Lead, model: str, host: str) -> Lead:
    """Fetch website text and ask Ollama for the owner name."""
    content = fetch_website_text(lead.website)
    owner = find_owner(content, model=model, host=host)
    lead.owner = owner
    return lead


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich gym leads with owner names via Ollama")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="", help="Output CSV path (default: input file overwritten)")
    parser.add_argument("--workers", type=int, default=3, help="Parallel workers (default: 3)")
    parser.add_argument("--model", default="mistral:7b", help="Ollama model (default: mistral:7b)")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host")
    args = parser.parse_args()

    output_path = args.output or args.input

    leads = _read_leads_csv(args.input)

    to_enrich: list[Lead] = []
    skipped_no_website = 0
    skipped_already_done = 0

    for lead in leads:
        if lead.owner:
            skipped_already_done += 1
        elif not lead.website:
            skipped_no_website += 1
        else:
            to_enrich.append(lead)

    print(f"[enrich] {len(leads)} total leads — {len(to_enrich)} to enrich, "
          f"{skipped_already_done} already enriched, {skipped_no_website} without website")

    enriched_count = 0
    unknown_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_enrich_lead, lead, args.model, args.host): lead
            for lead in to_enrich
        }
        for future in as_completed(futures):
            lead = futures[future]
            try:
                updated = future.result()
                owner = updated.owner
            except Exception as e:
                print(f"[enrich] ERROR enriching '{lead.name}': {e}")
                owner = ""

            if owner == "Unknown":
                unknown_count += 1
            elif owner:
                enriched_count += 1

            print(f"[enrich] {lead.name!r} -> {owner!r}")

    write_leads_csv(leads, output_path)

    print(f"\n[enrich] Done — {enriched_count} enriched, "
          f"{skipped_no_website} skipped (no website), {unknown_count} unknown")
    print(f"[enrich] Wrote {output_path}")


if __name__ == "__main__":
    main()

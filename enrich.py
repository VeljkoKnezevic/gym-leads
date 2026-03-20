"""Enrich gym lead CSVs with owner names via a local Ollama model.

Usage:
    python enrich.py --input output/ashburn-va-leads.csv
    python enrich.py --input leads.csv --output leads-enriched.csv --workers 2 --model mistral:7b
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

# Windows terminals default to cp1252 which chokes on non-ASCII gym names
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scrapers.base import Lead, CSV_COLUMNS
from utils.csv_writer import write_leads_csv
from utils.website_fetcher import fetch_website_text
from utils.website_resolver import resolve_website
from utils.ollama_client import find_owner
from utils.name_validator import validate_owner_name


def _read_leads_csv(path: str) -> list[Lead]:
    """Read a CSV into Lead objects. Tolerates CSVs without owner/confidence columns."""
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


def _enrich_lead(lead: Lead, model: str, host: str, confidence_threshold: float) -> Lead:
    """Multi-strategy enrichment pipeline for a single lead."""
    original_url = lead.website
    strategy_used = "none"

    # Step 1: Resolve platform URL to real website
    resolved_url = resolve_website(original_url, lead.name)
    url_was_resolved = resolved_url != original_url

    # Step 2: Try website text extraction + LLM on resolved URL
    best_name = "Unknown"
    best_confidence = 0.0

    if resolved_url:
        content = fetch_website_text(resolved_url)
        if content.strip():
            name, confidence = find_owner(
                content,
                gym_name=lead.name,
                gym_type=lead.type,
                city=lead.city,
                state=lead.state,
                model=model,
                host=host,
            )
            if name and name != "Unknown" and confidence >= confidence_threshold:
                best_name = name
                best_confidence = confidence
                strategy_used = "resolved_url" if url_was_resolved else "direct_url"

    # Step 3: If confidence is low and we resolved a URL, also try the original platform URL
    if best_confidence < 0.6 and url_was_resolved and original_url:
        content = fetch_website_text(original_url)
        if content.strip():
            name, confidence = find_owner(
                content,
                gym_name=lead.name,
                gym_type=lead.type,
                city=lead.city,
                state=lead.state,
                model=model,
                host=host,
            )
            if name and name != "Unknown" and confidence > best_confidence:
                best_name = name
                best_confidence = confidence
                strategy_used = "platform_url_fallback"

    # Step 4: Final validation
    if best_name and best_name != "Unknown":
        is_valid, reason = validate_owner_name(best_name, lead.name, lead.type)
        if not is_valid:
            print(f"  [enrich] Final validation rejected '{best_name}' for '{lead.name}': {reason}")
            best_name = "Unknown"
            best_confidence = 0.0
            strategy_used = "rejected"

    # Step 5: Set results
    lead.owner = best_name
    lead.owner_confidence = str(best_confidence) if best_confidence > 0 else ""

    status = f"owner={best_name!r} conf={best_confidence:.2f} via={strategy_used}"
    print(f"  [enrich] {lead.name!r} -> {status}")

    return lead


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich gym leads with owner names via Ollama")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="", help="Output CSV path (default: input file overwritten)")
    parser.add_argument("--workers", type=int, default=2, help="Parallel workers (default: 2)")
    parser.add_argument("--model", default="gpt-oss:20b", help="Ollama model (default: gpt-oss:20b)")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host")
    parser.add_argument("--confidence-threshold", type=float, default=0.4,
                        help="Minimum confidence to accept a name (default: 0.4)")
    args = parser.parse_args()

    output_path = args.output or args.input

    leads = _read_leads_csv(args.input)

    to_enrich: list[Lead] = []
    skipped_no_website = 0
    skipped_already_done = 0

    for lead in leads:
        if lead.owner and lead.owner != "Unknown":
            skipped_already_done += 1
        elif not lead.website:
            skipped_no_website += 1
        else:
            to_enrich.append(lead)

    print(f"[enrich] {len(leads)} total leads — {len(to_enrich)} to enrich, "
          f"{skipped_already_done} already enriched, {skipped_no_website} without website")
    print(f"[enrich] Confidence threshold: {args.confidence_threshold}")

    enriched_count = 0
    unknown_count = 0
    rejected_count = 0
    source_stats: Counter = Counter()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_enrich_lead, lead, args.model, args.host, args.confidence_threshold): lead
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

            if owner and owner != "Unknown":
                enriched_count += 1
                source_stats[lead.source] += 1
            elif owner == "Unknown":
                unknown_count += 1
            else:
                rejected_count += 1

    write_leads_csv(leads, output_path)

    # Summary
    total_with_owners = sum(1 for l in leads if l.owner and l.owner != "Unknown")
    print(f"\n[enrich] === Summary ===")
    print(f"[enrich] Newly enriched: {enriched_count}")
    print(f"[enrich] Unknown (no owner found): {unknown_count}")
    print(f"[enrich] Skipped (already done): {skipped_already_done}")
    print(f"[enrich] Skipped (no website): {skipped_no_website}")
    print(f"[enrich] Total leads with owners: {total_with_owners}/{len(leads)} "
          f"({100*total_with_owners/len(leads):.0f}%)" if leads else "")

    if source_stats:
        print(f"[enrich] Success by source: {dict(source_stats)}")

    print(f"[enrich] Wrote {output_path}")


if __name__ == "__main__":
    main()

"""Enrich gym lead CSVs with owner names via web search + local Ollama model.

Strategy:
  1. Try gym's own website (cached text from prefetch step, truncated to 5000 chars)
  2. If that fails, fire a single Serper query:
       "{gym}" "{city}" owner OR founder OR "owned by"

Usage:
    python enrich.py --input output/ashburn-va-leads.csv
    python enrich.py --input leads.csv --output leads-enriched.csv --workers 2 --model qwen3:14b
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Windows terminals default to cp1252 which chokes on non-ASCII gym names
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scrapers.base import Lead, CSV_COLUMNS
from utils.csv_writer import read_leads_csv, write_leads_csv
from utils.website_fetcher import fetch_website_text
from utils.ollama_client import find_owner
from utils.name_validator import validate_owner_name
from utils.cache import get_cache_path, DEFAULT_CACHE_DIR

# Load .env if present
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SERPER_SEARCH_URL = "https://google.serper.dev/search"
WEBSITE_CONTENT_CHAR_LIMIT = 5000  # Truncate LLM input — owner info is ~always in the first ~3-5KB


def _get_content(lead: Lead, url: str, cache_dir: str) -> str:
    """Return website content — from local cache if available, else fetch live."""
    cache_file = get_cache_path(lead, cache_dir)
    if cache_file.exists() and cache_file.stat().st_size > 100:
        return cache_file.read_text(encoding="utf-8")
    return fetch_website_text(url)


def _run_serper_query(query: str, api_key: str) -> str:
    """Run one Serper query, return concatenated snippets + KG text, or empty string."""
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    try:
        resp = requests.post(
            SERPER_SEARCH_URL,
            json={"q": query, "num": 5},
            headers=headers,
            timeout=15,
        )
        data = resp.json()
    except Exception as e:
        print(f"  [enrich] Serper query failed ({query[:60]!r}): {e}")
        return ""

    chunks: list[str] = []

    kg = data.get("knowledgeGraph", {})
    if kg:
        desc = kg.get("description", "")
        attrs = kg.get("attributes", {})
        if desc:
            chunks.append(f"[KNOWLEDGE GRAPH] {desc}")
        for k, v in attrs.items():
            chunks.append(f"{k}: {v}")

    for result in data.get("organic", [])[:5]:
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        if snippet:
            chunks.append(f"[SEARCH RESULT] {title}: {snippet}")

    for paa in data.get("peopleAlsoAsk", [])[:3]:
        snippet = paa.get("snippet", "")
        if snippet:
            chunks.append(f"[PAA] {snippet}")

    return "\n".join(chunks)


def _enrich_lead(lead: Lead, model: str, serper_model: str, host: str,
                 confidence_threshold: float,
                 cache_dir: str = DEFAULT_CACHE_DIR) -> Lead:
    """Multi-strategy enrichment pipeline for a single lead."""
    strategy_used = "none"
    best_name = "Unknown"
    best_confidence = 0.0

    # Step 1: Try website text extraction + LLM (prefer cache)
    if lead.website:
        content = _get_content(lead, lead.website, cache_dir)
        if content.strip():
            name, confidence = find_owner(
                content[:WEBSITE_CONTENT_CHAR_LIMIT],
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
                strategy_used = "website"

    # Step 2: If website failed, fire a single Serper query.
    if best_name == "Unknown" or not best_name:
        api_key = os.environ.get("SERPER_API_KEY", "")
        if api_key:
            query = f'"{lead.name}" "{lead.city}" owner OR founder OR "owned by"'
            search_content = _run_serper_query(query, api_key)
            if search_content.strip():
                name, confidence = find_owner(
                    search_content,
                    gym_name=lead.name,
                    gym_type=lead.type,
                    city=lead.city,
                    state=lead.state,
                    model=serper_model,
                    host=host,
                )
                if name and name != "Unknown" and confidence >= confidence_threshold:
                    best_name = name
                    best_confidence = confidence
                    strategy_used = "serper"

    # Step 3: Set owner results (individual names already validated in ollama_client)
    lead.owner = best_name if best_name != "Unknown" else ""
    lead.owner_confidence = str(best_confidence) if best_confidence > 0 else ""

    status = f"owner={best_name!r} conf={best_confidence:.2f} via={strategy_used}"
    print(f"  [enrich] {lead.name!r} -> {status}")

    return lead


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich gym leads with owner names via Ollama")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="", help="Output CSV path (default: input file overwritten)")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument("--model", default="qwen3:14b",
                        help="Ollama model for website extraction (default: qwen3:14b)")
    parser.add_argument("--serper-model", default="qwen3:8b",
                        help="Ollama model for Serper-snippet extraction (default: qwen3:8b)")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host")
    parser.add_argument("--confidence-threshold", type=float, default=0.4,
                        help="Minimum confidence to accept a name (default: 0.4)")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                        help=f"Website cache directory (default: {DEFAULT_CACHE_DIR})")
    args = parser.parse_args()

    output_path = args.output or args.input

    leads = read_leads_csv(args.input)

    to_enrich: list[Lead] = []
    skipped_already_done = 0

    for lead in leads:
        if lead.owner:
            skipped_already_done += 1
        else:
            to_enrich.append(lead)

    has_serper = bool(os.environ.get("SERPER_API_KEY", ""))
    print(f"[enrich] {len(leads)} total leads — {len(to_enrich)} to enrich, "
          f"{skipped_already_done} already enriched")
    print(f"[enrich] Strategy: website first ({args.model})"
          f"{', then Serper (' + args.serper_model + ')' if has_serper else ''}")
    print(f"[enrich] Confidence threshold: {args.confidence_threshold}")

    enriched_count = 0
    unknown_count = 0
    source_stats: Counter = Counter()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_enrich_lead, lead, args.model, args.serper_model,
                            args.host, args.confidence_threshold, args.cache_dir): lead
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

            if owner:
                enriched_count += 1
                source_stats[lead.source] += 1
            else:
                unknown_count += 1

    write_leads_csv(leads, output_path)

    # Summary
    total_with_owners = sum(1 for l in leads if l.owner)
    print(f"\n[enrich] === Summary ===")
    print(f"[enrich] Newly enriched: {enriched_count}")
    print(f"[enrich] Unknown (no owner found): {unknown_count}")
    print(f"[enrich] Skipped (already done): {skipped_already_done}")
    print(f"[enrich] Total leads with owners: {total_with_owners}/{len(leads)} "
          f"({100*total_with_owners/len(leads):.0f}%)" if leads else "")

    if source_stats:
        print(f"[enrich] Success by source: {dict(source_stats)}")

    print(f"[enrich] Wrote {output_path}")


if __name__ == "__main__":
    main()

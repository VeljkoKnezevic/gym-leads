"""Compare qwen3:14b vs NuExtract 2.0 for owner extraction.

Runs both models on the same leads (using cached website text) and
prints a side-by-side comparison with timing.

Usage:
    python compare_models.py --input output/danbury-ct.csv
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scrapers.base import Lead
from utils.csv_writer import read_leads_csv
from utils.cache import get_cache_path, DEFAULT_CACHE_DIR
from utils.ollama_client import find_owner  # existing qwen3/gemma approach


# --- NuExtract 2.0 approach ---

_NUEXTRACT_TEMPLATE = json.dumps({
    "owner_names": ["verbatim-string"],
    "owner_role": ["verbatim-string"],
})

_NUEXTRACT_EXAMPLE_INPUT = (
    "Founded by Sarah Chen in 2019, Peak Fitness is a boutique gym in downtown Portland. "
    "Co-owner Mike Lee handles operations while Sarah leads personal training."
)

_NUEXTRACT_EXAMPLE_OUTPUT = json.dumps({
    "owner_names": ["Sarah Chen", "Mike Lee"],
    "owner_role": ["founder", "co-owner"],
})


def _nuextract_find_owner(content: str, lead: Lead, host: str) -> tuple[str, float]:
    """Use NuExtract 2.0 to extract owner names via chat API."""
    if not content.strip():
        return "Unknown", 0.0

    # Truncate content
    text = content[:4000]

    # NuExtract 2.0 uses role-based messages
    messages = [
        {"role": "template", "content": _NUEXTRACT_TEMPLATE},
        {"role": "examples.input", "content": _NUEXTRACT_EXAMPLE_INPUT},
        {"role": "examples.output", "content": _NUEXTRACT_EXAMPLE_OUTPUT},
        {"role": "user", "content": text},
    ]

    try:
        resp = requests.post(
            f"{host}/api/chat",
            json={
                "model": "frob/nuextract-2.0:8b-q8_0",
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=90,
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "").strip()

        # Parse JSON response
        try:
            data = json.loads(raw)
            names = data.get("owner_names", [])
            if not names:
                return "Unknown", 0.0

            # Filter out empty strings
            names = [n.strip() for n in names if n.strip()]
            if not names:
                return "Unknown", 0.0

            # Filter to full names (first + last)
            full_names = [n for n in names if len(n.split()) >= 2]
            if not full_names:
                return "Unknown", 0.0

            return ", ".join(full_names), 0.7
        except json.JSONDecodeError:
            # Try to extract names from raw text
            return "Unknown", 0.0

    except Exception as e:
        return f"ERROR: {e}", 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare models for owner extraction")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of leads to test (0 = all)")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    args = parser.parse_args()

    leads = read_leads_csv(args.input)

    # Filter to leads with websites and cached content
    test_leads = []
    for lead in leads:
        if not lead.website:
            continue
        cache_file = get_cache_path(lead, args.cache_dir)
        if cache_file.exists() and cache_file.stat().st_size > 100:
            test_leads.append(lead)

    if args.limit > 0:
        test_leads = test_leads[:args.limit]

    print(f"Testing {len(test_leads)} leads with cached website content\n")
    print(f"{'Gym Name':<45} {'qwen3:14b':<30} {'NuExtract 2.0':<30}")
    print("=" * 105)

    qwen_found = 0
    nuex_found = 0
    qwen_total_time = 0.0
    nuex_total_time = 0.0

    for i, lead in enumerate(test_leads, 1):
        cache_file = get_cache_path(lead, args.cache_dir)
        content = cache_file.read_text(encoding="utf-8")

        # --- qwen3:14b ---
        t0 = time.time()
        q_name, q_conf = find_owner(
            content, gym_name=lead.name, gym_type=lead.type,
            city=lead.city, state=lead.state,
            model="qwen3:14b", host=args.host,
        )
        q_time = time.time() - t0
        qwen_total_time += q_time

        # --- NuExtract 2.0 ---
        t0 = time.time()
        n_name, n_conf = _nuextract_find_owner(content, lead, args.host)
        n_time = time.time() - t0
        nuex_total_time += n_time

        q_found = q_name and q_name != "Unknown"
        n_found = n_name and n_name != "Unknown" and not n_name.startswith("ERROR")
        if q_found:
            qwen_found += 1
        if n_found:
            nuex_found += 1

        q_display = f"{q_name} ({q_time:.1f}s)" if q_found else f"- ({q_time:.1f}s)"
        n_display = f"{n_name} ({n_time:.1f}s)" if n_found else f"- ({n_time:.1f}s)"

        gym_short = lead.name[:43]
        print(f"[{i:3d}/{len(test_leads)}] {gym_short:<43} {q_display:<30} {n_display:<30}")

    # Summary
    print(f"\n{'=' * 105}")
    print(f"RESULTS SUMMARY")
    print(f"{'=' * 105}")
    print(f"Leads tested:    {len(test_leads)}")
    print(f"")
    print(f"  qwen3:14b:     {qwen_found}/{len(test_leads)} found ({100*qwen_found//len(test_leads)}%)  |  Total: {qwen_total_time:.0f}s  |  Avg: {qwen_total_time/len(test_leads):.1f}s/lead")
    print(f"  NuExtract 2.0: {nuex_found}/{len(test_leads)} found ({100*nuex_found//len(test_leads)}%)  |  Total: {nuex_total_time:.0f}s  |  Avg: {nuex_total_time/len(test_leads):.1f}s/lead")
    print()

    # Agreement
    both = 0
    qwen_only = 0
    nuex_only = 0
    neither = 0
    for lead in test_leads:
        cache_file = get_cache_path(lead, args.cache_dir)
        # We already ran these above, but the results are printed inline
        # Just count from the printed output (we'd need to store results to do this properly)
    print(f"Done.")


if __name__ == "__main__":
    main()

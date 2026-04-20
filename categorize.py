"""Categorize gym leads by type using a local Ollama model.

Reads cached website text and produces natural labels like
"pilates studio", "CrossFit gym", "yoga studio", etc.

Usage:
    python categorize.py --input output/altamonte-springs-fl.csv
    python categorize.py --input output/altamonte-springs-fl.csv --model qwen3:14b
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scrapers.base import Lead
from utils.csv_writer import read_leads_csv, write_leads_csv
from utils.cache import get_cache_path, DEFAULT_CACHE_DIR

_PROMPT = """\
You are categorizing a gym/fitness business for a cold email campaign.

Output a short SINGULAR category label (1-3 words) that names the discipline \
this place is known for. The label must sound natural in: "I help [category] owners like yourself get more members".

## Rules
1. Output ONLY the label — nothing else. SINGULAR form, never plural.
2. Lowercase, unless a proper noun (CrossFit, HIIT).
3. ONLY use a niche label when the gym is clearly specialized in one of these:
   barre studio, pilates studio, yoga studio, CrossFit gym, HIIT studio, \
boxing gym, kickboxing gym, martial arts gym, jiu-jitsu gym, muay thai gym, \
cycling studio, spin studio, dance studio, powerlifting gym, personal training studio, \
climbing gym, hyrox gym
4. If it's a general fitness place with no single clear niche → output "gym".
5. If it's a general studio-style place (small, classes-based, no one niche) → output "fitness studio".
6. NEVER use adjectives like "24/7", "general", "group", "functional", "performance-based", \
"high intensity", "group training", "group fitness", "human optimization". \
Strip them off — "24/7 gym" -> "gym", "functional fitness studio" -> "gym", \
"group fitness studio" -> "fitness studio", "performance-based fitness studio" -> "fitness studio".

## Examples
Gym: "CrossFit Down To Earth" / Website mentions WODs, Olympic lifting, CrossFit classes
Output: CrossFit gym

Gym: "Club Pilates Altamonte Springs" / Website mentions reformer, Pilates method, classes
Output: pilates studio

Gym: "Orangetheory Fitness" / Website mentions heart rate training, treadmills, rowing
Output: fitness studio

Gym: "Pure Barre" / Website mentions barre, low-impact, ballet-inspired
Output: barre studio

Gym: "Anytime Fitness" / Website mentions 24/7 access, cardio equipment, weights
Output: gym

Gym: "F45 Training" / Website mentions functional fitness, team training, 45-min classes
Output: gym

Gym: "Zen Yoga Studio" / Website mentions vinyasa, hot yoga, meditation
Output: yoga studio

Gym: "Iron Forge" / Website mentions powerlifting, barbells, strongman
Output: powerlifting gym

Gym: "CycleBar" / Website mentions indoor cycling, spin classes, rides
Output: cycling studio

Gym: "Title Boxing Club" / Website mentions boxing, kickboxing, heavy bags
Output: boxing gym

Gym: "Planet Fitness" / Website mentions cardio, weights, judgement-free
Output: gym

## Now categorize:
Gym: "{gym_name}"
Website text (first 1500 chars): {website_text}
Output:"""


def _get_content(lead: Lead, cache_dir: str) -> str:
    """Read cached website text for a lead."""
    cache_file = get_cache_path(lead, cache_dir)
    if cache_file.exists() and cache_file.stat().st_size > 100:
        return cache_file.read_text(encoding="utf-8", errors="replace")
    return ""


def _categorize_lead(lead: Lead, model: str, host: str,
                     cache_dir: str) -> tuple[Lead, str]:
    """Categorize a single lead using Ollama."""
    if lead.gym_category:
        return lead, "already_done"

    content = _get_content(lead, cache_dir)
    if not content.strip():
        # Fallback: use gym name + type field
        content = f"{lead.name} - {lead.type}"

    # Truncate to keep prompt small and fast
    website_text = content[:1500]

    prompt = _PROMPT.format(
        gym_name=lead.name,
        website_text=website_text,
    )

    try:
        resp = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "think": False},
            timeout=60,
        )
        if resp.status_code != 200:
            return lead, "api_error"
        raw = resp.json().get("response", "").strip()
    except Exception as e:
        return lead, f"error: {e}"

    # Clean up response
    category = raw.strip().strip('"').strip("'").strip(".")
    # Remove any prefix the model might add
    for prefix in ("Output:", "output:", "Category:", "category:"):
        if category.lower().startswith(prefix.lower()):
            category = category[len(prefix):].strip()

    # Take only the first line if model over-generates
    category = category.split("\n")[0].strip()

    # Cap length
    if len(category) > 50:
        category = category[:50].rsplit(" ", 1)[0]

    if category:
        lead.gym_category = category
        return lead, "found"

    return lead, "empty_response"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Categorize gym leads by type using Ollama")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="",
                        help="Output CSV path (default: overwrite input)")
    parser.add_argument("--workers", type=int, default=2,
                        help="Parallel workers (default: 2)")
    parser.add_argument("--model", default="qwen3:14b",
                        help="Ollama model (default: qwen3:14b)")
    parser.add_argument("--host", default="http://localhost:11434",
                        help="Ollama host")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                        help=f"Website cache directory (default: {DEFAULT_CACHE_DIR})")
    args = parser.parse_args()

    output_path = args.output or args.input
    leads = read_leads_csv(args.input)

    to_process = [l for l in leads if not l.gym_category]
    already_done = len(leads) - len(to_process)

    print(f"[categorize] {len(leads)} total leads — {len(to_process)} to categorize, "
          f"{already_done} already done")
    print(f"[categorize] Model: {args.model}")

    stats: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_categorize_lead, lead, args.model, args.host, args.cache_dir): lead
            for lead in to_process
        }
        for i, future in enumerate(as_completed(futures), 1):
            orig_lead = futures[future]
            try:
                updated_lead, status = future.result()
                stats[status] = stats.get(status, 0) + 1
                if status == "found":
                    print(f"[categorize] [{i}/{len(to_process)}] {updated_lead.name!r} "
                          f"-> {updated_lead.gym_category}")
                else:
                    print(f"[categorize] [{i}/{len(to_process)}] {updated_lead.name!r} "
                          f"-> {status}")
            except Exception as e:
                print(f"[categorize] [{i}/{len(to_process)}] ERROR on "
                      f"'{orig_lead.name}': {e}")
                stats["error"] = stats.get("error", 0) + 1

    write_leads_csv(leads, output_path)

    total_with_category = sum(1 for l in leads if l.gym_category)
    print(f"\n[categorize] === Summary ===")
    for k, v in sorted(stats.items()):
        print(f"[categorize]   {k}: {v}")
    print(f"[categorize] Total categorized: {total_with_category}/{len(leads)} "
          f"({100*total_with_category//len(leads)}%)" if leads else "")
    print(f"[categorize] Output: {output_path}")


if __name__ == "__main__":
    main()

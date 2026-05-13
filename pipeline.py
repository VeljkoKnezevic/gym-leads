"""Full gym lead pipeline: scrape → prefetch → enrich → categorize.

Uses gemma3:27b for structured extraction (owner names) and
qwen3:14b for creative tasks (gym category).

Usage:
    python pipeline.py --city "Danbury, CT"
    python pipeline.py --city "Danbury, CT" --skip-enrich
    python pipeline.py --city "Danbury, CT" --workers 6
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path


def city_to_slug(city: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", city.lower()).strip("-")


def run_step(label: str, cmd: list[str]) -> int:
    """Run a subprocess step. Streams output. Returns exit code."""
    print(f"\n{'='*60}")
    print(f"  STAGE: {label}")
    print(f"  CMD:   {' '.join(cmd)}")
    print(f"{'='*60}\n")
    start = time.time()
    result = subprocess.run(cmd, check=False)
    elapsed = time.time() - start
    status = "OK" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    print(f"\n[pipeline] {label} -> {status} ({elapsed:.1f}s)")
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full gym lead pipeline for a city."
    )
    parser.add_argument("--city", required=True, help='e.g. "Danbury, CT"')
    parser.add_argument(
        "--sources", nargs="+",
        choices=["google_maps"],
        default=["google_maps"],
        help="Scrapers to run (default: google_maps)",
    )
    parser.add_argument("--workers", type=int, default=4,
                        help="Workers for prefetch (default: 4)")
    parser.add_argument("--enrich-workers", type=int, default=4,
                        help="Workers for enrich step (default: 4)")
    parser.add_argument("--enrich-model", default="qwen3:14b",
                        help="Ollama model for owner extraction (default: qwen3:14b)")
    parser.add_argument("--creative-model", default="qwen3:14b",
                        help="Ollama model for categorize step (default: qwen3:14b)")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="Skip scrape step (use existing leads file)")
    parser.add_argument("--skip-prefetch", action="store_true",
                        help="Skip prefetch step")
    parser.add_argument("--skip-enrich", action="store_true",
                        help="Skip enrich step")
    parser.add_argument("--skip-categorize", action="store_true",
                        help="Skip categorize step")
    parser.add_argument("--maps-pages", type=int, default=10,
                        help="Max Google Maps pages per query during scrape (default: 10)")
    parser.add_argument("--headed", action="store_true",
                        help="Run browsers in headed mode (scrape step)")
    parser.add_argument("--sequential", action="store_true",
                        help="Run scrapers sequentially (lower memory)")
    args = parser.parse_args()

    slug = city_to_slug(args.city)
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    final_file      = str(output_dir / f"{slug}.csv")
    # Intermediate file — cleaned up after pipeline completes
    leads_file      = str(output_dir / f"{slug}-leads.csv")

    py = sys.executable
    pipeline_start = time.time()
    stages_run: list[tuple[str, int]] = []

    # --- Stage 1: Scrape ---
    if not args.skip_scrape:
        cmd = [py, "scrape.py", "--city", str(args.city), "--output", leads_file,
               "--maps-pages", str(args.maps_pages)]
        if args.sources:
            cmd += ["--sources"] + args.sources
        if args.headed:
            cmd.append("--headed")
        if args.sequential:
            cmd.append("--sequential")
        rc = run_step("scrape", cmd)
        stages_run.append(("scrape", rc))
        if rc != 0:
            print(f"[pipeline] Scrape failed — aborting.", file=sys.stderr)
            sys.exit(rc)
    else:
        if not Path(leads_file).exists():
            print(f"[pipeline] --skip-scrape set but {leads_file} not found.", file=sys.stderr)
            sys.exit(1)
        print(f"[pipeline] Skipping scrape — using {leads_file}")

    # --- Stage 2: Prefetch (websites, socials) ---
    if not args.skip_prefetch:
        cmd = [py, "prefetch.py", "--input", leads_file,
               "--output", final_file, "--workers", str(args.workers)]
        rc = run_step("prefetch", cmd)
        stages_run.append(("prefetch", rc))
        if rc != 0:
            print(f"[pipeline] Prefetch failed — aborting.", file=sys.stderr)
            sys.exit(rc)
    else:
        if not Path(final_file).exists():
            import shutil
            shutil.copy2(leads_file, final_file)
        print(f"[pipeline] Skipping prefetch — using {final_file}")

    # --- Stage 3: Enrich — owner names (gemma3:27b) ---
    if not args.skip_enrich:
        cmd = [py, "enrich.py", "--input", final_file,
               "--workers", str(args.enrich_workers),
               "--model", args.enrich_model]
        rc = run_step("enrich", cmd)
        stages_run.append(("enrich", rc))
        if rc != 0:
            print(f"[pipeline] Enrich failed.")
    else:
        print(f"[pipeline] Skipping enrich.")

    # --- Stage 4: Categorize — gym type (qwen3:14b) ---
    if not args.skip_categorize:
        cmd = [py, "categorize.py", "--input", final_file,
               "--workers", str(args.enrich_workers),
               "--model", args.creative_model]
        rc = run_step("categorize", cmd)
        stages_run.append(("categorize", rc))
        if rc != 0:
            print(f"[pipeline] Categorize failed.")
    else:
        print(f"[pipeline] Skipping categorize.")

    # Reviews stage intentionally disabled. Current workflow stops after
    # owner enrichment and categorization.

    # Clean up intermediate files
    p = Path(leads_file)
    if p.exists():
        p.unlink()

    total = time.time() - pipeline_start
    print(f"\n{'='*60}")
    print(f"  PIPELINE COMPLETE — {total:.1f}s total")
    for name, rc in stages_run:
        status = "OK" if rc == 0 else f"FAILED ({rc})"
        print(f"    {name:12s} {status}")
    print(f"\n  Output: {final_file}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()

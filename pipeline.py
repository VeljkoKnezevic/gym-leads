"""Full gym lead pipeline: scrape → prefetch → ad_check → enrich.

Ad check uses pixel detection (instant, no browser needed) to filter
leads running ads, then enrich only runs on that smaller set.

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
        choices=["mindbody", "crossfit", "google_maps", "hyrox"],
        help="Scrapers to run (default: all)",
    )
    parser.add_argument("--workers", type=int, default=4,
                        help="Workers for prefetch (default: 4)")
    parser.add_argument("--enrich-workers", type=int, default=2,
                        help="Workers for enrich step (default: 2)")
    parser.add_argument("--model", default="gpt-oss:20b",
                        help="Ollama model for enrich (default: gpt-oss:20b)")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="Skip scrape step (use existing leads file)")
    parser.add_argument("--skip-prefetch", action="store_true",
                        help="Skip prefetch step")
    parser.add_argument("--skip-enrich", action="store_true",
                        help="Skip enrich step")
    parser.add_argument("--skip-ads", action="store_true",
                        help="Skip ad_check step")
    parser.add_argument("--headed", action="store_true",
                        help="Run browsers in headed mode (scrape step)")
    parser.add_argument("--sequential", action="store_true",
                        help="Run scrapers sequentially (lower memory)")
    args = parser.parse_args()

    slug = city_to_slug(args.city)
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    final_file      = str(output_dir / f"{slug}.csv")
    # Intermediate files — cleaned up after pipeline completes
    leads_file      = str(output_dir / f"{slug}-leads.csv")
    prefetched_file = str(output_dir / f"{slug}-prefetched.csv")

    py = sys.executable
    pipeline_start = time.time()
    stages_run: list[tuple[str, int]] = []

    # --- Stage 1: Scrape ---
    if not args.skip_scrape:
        cmd = [py, "scrape.py", "--city", str(args.city), "--output", leads_file]
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

    # --- Stage 2: Prefetch (websites, socials, emails, pixel detection) ---
    if not args.skip_prefetch:
        cmd = [py, "prefetch.py", "--input", leads_file,
               "--output", prefetched_file, "--workers", str(args.workers)]
        rc = run_step("prefetch", cmd)
        stages_run.append(("prefetch", rc))
        if rc != 0:
            print(f"[pipeline] Prefetch failed — aborting.", file=sys.stderr)
            sys.exit(rc)
    else:
        if not Path(prefetched_file).exists():
            prefetched_file = leads_file
        print(f"[pipeline] Skipping prefetch — using {prefetched_file}")

    # --- Stage 3: Ad Check (pixel-based, instant) → final output ---
    if not args.skip_ads:
        cmd = [py, "ad_check.py", "--input", prefetched_file, "--output", final_file]
        rc = run_step("ad_check", cmd)
        stages_run.append(("ad_check", rc))
        if rc != 0:
            print(f"[pipeline] Ad check failed — aborting.", file=sys.stderr)
            sys.exit(rc)
        if not Path(final_file).exists():
            print(f"[pipeline] No output — no leads running ads found.")
            sys.exit(0)
    else:
        print(f"[pipeline] Skipping ad_check.")

    # --- Stage 4: Enrich (only on leads running ads) ---
    if not args.skip_enrich:
        cmd = [py, "enrich.py", "--input", final_file,
               "--workers", str(args.enrich_workers), "--model", args.model]
        rc = run_step("enrich", cmd)
        stages_run.append(("enrich", rc))
        if rc != 0:
            print(f"[pipeline] Enrich failed.")
    else:
        print(f"[pipeline] Skipping enrich.")

    # Clean up intermediate files
    for tmp in (leads_file, prefetched_file):
        p = Path(tmp)
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

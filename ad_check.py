"""Check gym leads for active ads via pixel detection.

Checks for ad tracking pixels (Meta, Google, TikTok) detected during
prefetch. Pixels are found in the website HTML and stored in ad_pixels.

Outputs one file: leads where running_ads=true (has ad pixels).

Usage:
    python ad_check.py --input output/danbury-ct-leads-prefetched.csv
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scrapers.base import Lead
from utils.csv_writer import read_leads_csv, write_leads_csv
from utils.geo import STATE_ABBR, ABBR_TO_STATE


def main() -> None:
    parser = argparse.ArgumentParser(description="Check gym leads for ads via pixel detection")
    parser.add_argument("--input", required=True, help="Input CSV path")
    args = parser.parse_args()

    stem = Path(args.input).stem
    parent = Path(args.input).parent
    ads_output = str(parent / f"{stem}-ads.csv")

    leads = read_leads_csv(args.input)

    # State filter
    state_counts = Counter(l.state.strip() for l in leads if l.state.strip())
    if state_counts:
        target_state_raw = state_counts.most_common(1)[0][0]
        if len(target_state_raw) == 2:
            target_abbr = target_state_raw.upper()
            target_full = ABBR_TO_STATE.get(target_abbr, target_abbr).title()
        else:
            target_full = target_state_raw.title()
            target_abbr = STATE_ABBR.get(target_full, "").upper()

        before = len(leads)
        leads = [
            l for l in leads
            if not l.state
            or l.state.strip().upper() in (target_abbr, target_full.upper())
            or l.state.strip().title() == target_full
        ]
        removed = before - len(leads)
        if removed:
            print(f"[ad_check] State filter ({target_full}): removed {removed} leads, {len(leads)} remain")

    # Mark leads as running ads based on pixel detection (done during prefetch)
    for lead in leads:
        if lead.ad_pixels:
            lead.running_ads = "true"
        else:
            lead.running_ads = "false"

    running = [l for l in leads if l.running_ads == "true"]

    # Print results
    print(f"[ad_check] {len(leads)} total leads")
    for lead in leads:
        status = f"pixels={lead.ad_pixels}" if lead.ad_pixels else "no pixels"
        print(f"[ad_check]   {lead.name!r} -> {status} running={lead.running_ads}")

    write_leads_csv(running, ads_output)

    # Summary
    pixel_types = Counter()
    for l in running:
        for p in l.ad_pixels.split(","):
            if p.strip():
                pixel_types[p.strip()] += 1

    print(f"\n[ad_check] Done — {len(running)}/{len(leads)} running ads (pixel detected)")
    if pixel_types:
        for ptype, count in pixel_types.most_common():
            print(f"[ad_check]   {ptype}: {count}")
    print(f"[ad_check] Output: {ads_output}")


if __name__ == "__main__":
    main()

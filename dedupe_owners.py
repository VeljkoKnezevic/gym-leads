"""Deduplicate an enriched lead CSV by owner name.

Rows are merged when they share any exact full owner name. This handles both
single owners and owner lists, e.g. "Jason Corbitt, Patrick Bresley" matches a
row whose owner is just "Jason Corbitt".
"""

from __future__ import annotations

import argparse

from utils.csv_writer import read_leads_csv, write_leads_csv
from utils.dedup import deduplicate_by_owner


def main() -> None:
    parser = argparse.ArgumentParser(description="Deduplicate leads by owner name")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="", help="Output CSV path (default: overwrite input)")
    args = parser.parse_args()

    output_path = args.output or args.input
    leads = read_leads_csv(args.input)
    deduped = deduplicate_by_owner(leads)
    write_leads_csv(deduped, output_path)

    removed = len(leads) - len(deduped)
    print(f"[owner-dedupe] Input: {len(leads)}")
    print(f"[owner-dedupe] Removed: {removed}")
    print(f"[owner-dedupe] Output: {len(deduped)}")
    print(f"[owner-dedupe] Wrote {output_path}")


if __name__ == "__main__":
    main()

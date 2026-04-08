"""CSV reader/writer for leads."""

import csv
import os
import re

from scrapers.base import Lead, CSV_COLUMNS, normalize_phone


def clean_name(name: str) -> str:
    """Remove location code suffixes added by booking platforms.

    Strips patterns like:
      "Orangetheory Fitness Ashburn #0196"  -> "Orangetheory Fitness Ashburn"
      "Elements Massage Ashburn, EM-VA-20005" -> "Elements Massage Ashburn"
      "SomeStudio DC.MD.VA"                 -> "SomeStudio"
    """
    name = re.sub(r"\s*#\w+$", "", name)                       # Remove #0196
    name = re.sub(r",?\s*[A-Z]{2}-[A-Z]{2}-\d+$", "", name)   # Remove EM-VA-20005
    name = re.sub(r"\s+[A-Z]{2}\.[A-Z]{2}\.[A-Z]{2}$", "", name)  # Remove DC.MD.VA
    return name.strip()


def read_leads_csv(path: str) -> list[Lead]:
    """Read a CSV into Lead objects. Tolerates CSVs with missing columns."""
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
                facebook_url=row.get("facebook_url", ""),
                instagram_url=row.get("instagram_url", ""),
                running_ads=row.get("running_ads", ""),
                ad_pixels=row.get("ad_pixels", ""),
            ))
    return leads


def write_leads_csv(leads: list[Lead], output_path: str) -> str:
    """Write leads to CSV file. Cleans names and normalizes phone numbers before writing.
    Returns the absolute path written."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for lead in leads:
            row = lead.to_dict()
            row["name"] = clean_name(row["name"])
            row["phone"] = normalize_phone(row["phone"])
            writer.writerow(row)

    return os.path.abspath(output_path)

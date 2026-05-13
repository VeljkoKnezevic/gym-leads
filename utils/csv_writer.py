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


def split_first_owner(owner: str) -> tuple[str, str]:
    """Return the first full owner name and first name from an owner list."""
    if not owner:
        return "", ""

    first_owner = re.split(r"\s*,\s*|\s+(?:and|&)\s+", owner.strip(), maxsplit=1)[0]
    first_owner = first_owner.strip()
    first_owner_name = first_owner.split()[0] if first_owner else ""
    return first_owner, first_owner_name


def first_word(value: str) -> str:
    """Return the first word with only the first letter capitalized."""
    value = value.strip()
    if not value:
        return ""
    word = value.split()[0].lower()
    return word[:1].upper() + word[1:]


def outcome_word_for_category(category: str) -> str:
    """Return email outcome word for a gym category."""
    if not category:
        return ""

    intro_keywords = (
        "barre",
        "boxing",
        "climbing",
        "CrossFit",
        "cycling",
        "dance",
        "HIIT",
        "hot yoga",
        "hyrox",
        "jiu-jitsu",
        "kickboxing",
        "martial arts",
        "muay thai",
        "pilates",
        "spin",
        "yoga",
    )
    category_lower = category.lower()
    if any(keyword.lower() in category_lower for keyword in intro_keywords):
        return "intros"
    return "consults"


def read_leads_csv(path: str) -> list[Lead]:
    """Read a CSV into Lead objects. Tolerates CSVs with missing columns."""
    def get(row: dict, *names: str) -> str:
        for name in names:
            value = row.get(name)
            if value not in (None, ""):
                return value
        return ""

    leads: list[Lead] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads.append(Lead(
                name=get(row, "name", "Name", "Company Name"),
                first_company_name=get(row, "first_company_name", "First Company Name"),
                address=get(row, "address", "Address"),
                city=get(row, "city", "City"),
                state=get(row, "state", "State"),
                phone=get(row, "phone", "Phone"),
                email=get(row, "email", "Email", "Work Email"),
                website=get(row, "website", "Website"),
                type=get(row, "type", "Type"),
                source=get(row, "source", "Source"),
                owner=get(
                    row,
                    "owner",
                    "Owner",
                    "Decision Maker Name full Name",
                    "Decision Maker Name",
                ),
                first_owner=get(row, "first_owner", "First Owner"),
                first_owner_name=get(row, "first_owner_name", "First Owner Name", "First Name"),
                owner_confidence=get(row, "owner_confidence", "Owner Confidence"),
                facebook_url=get(row, "facebook_url", "Facebook URL"),
                instagram_url=get(row, "instagram_url", "Instagram URL"),
                gym_category=get(row, "gym_category", "Gym Category"),
                outcome_word=get(row, "outcome_word", "Outcome Word"),
            ))
    return leads


def write_leads_csv(leads: list[Lead], output_path: str) -> str:
    """Write leads to CSV file. Cleans names and normalizes phone numbers before writing.
    Returns the absolute path written."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    original_rows: list[dict] = []
    fieldnames = list(CSV_COLUMNS)
    if os.path.exists(output_path):
        with open(output_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                fieldnames = list(reader.fieldnames)
                original_rows = list(reader)
                for col in CSV_COLUMNS:
                    if col not in fieldnames:
                        if col == "first_company_name" and "name" in fieldnames:
                            fieldnames.insert(fieldnames.index("name") + 1, col)
                        elif col == "outcome_word" and "gym_category" in fieldnames:
                            fieldnames.insert(fieldnames.index("gym_category") + 1, col)
                        else:
                            fieldnames.append(col)

    preserve_original_rows = len(original_rows) == len(leads)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, lead in enumerate(leads):
            row = dict(original_rows[i]) if preserve_original_rows and i < len(original_rows) else {}
            lead_row = lead.to_dict()
            lead_row["name"] = clean_name(lead_row["name"])
            lead_row["first_company_name"] = first_word(lead_row["name"])
            lead_row["phone"] = normalize_phone(lead_row["phone"])
            first_owner, first_owner_name = split_first_owner(lead_row.get("owner", ""))
            lead_row["first_owner"] = first_owner
            lead_row["first_owner_name"] = first_owner_name
            lead_row["outcome_word"] = outcome_word_for_category(lead_row.get("gym_category", ""))
            for col in CSV_COLUMNS:
                row[col] = lead_row.get(col, "")
            writer.writerow(row)

    return os.path.abspath(output_path)

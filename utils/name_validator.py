"""Validate extracted owner names — plausibility checks + franchise founder blocklist."""

from __future__ import annotations

import re

# Known franchise corporate founders — these are NOT local owners
KNOWN_FRANCHISE_FOUNDERS: dict[str, list[str]] = {
    "f45": ["Rob Deutsch", "Adam Gilchrist"],
    "fit body boot camp": ["Bedros Keuilian"],
    "orangetheory": ["Ellen Latham"],
    "crossfit": ["Greg Glassman"],
    "anytime fitness": ["Chuck Runyon", "Dave Mortensen"],
    "planet fitness": ["Michael Grondahl", "Marc Grondahl"],
    "gold's gym": ["Joe Gold"],
    "9round": ["Shannon Hudson"],
    "burn boot camp": ["Devan Kline"],
    "club pilates": ["Anthony Geisler"],
    "pure barre": ["Carrie Rezabek Dorr"],
    "solidcore": ["Anne Mahlum"],
    "cyclebar": ["Anthony Geisler"],
    "snap fitness": ["Peter Taunton"],
    "title boxing": ["Tom Lyons"],
    "hotworx": ["Stephen Smith"],
    "retro fitness": ["Eric Casaburi"],
    "crunch": ["Doug Levine"],
    "la fitness": ["Louis Welch", "Chin Yol Yi"],
    "lifetime": ["Bahram Akradi"],
    "life time": ["Bahram Akradi"],
    "equinox": ["Harvey Spevak"],
    "soulcycle": ["Julie Rice", "Elizabeth Cutler"],
    "barry's": ["Barry Jay"],
    "rumble boxing": ["Noah Neiman", "Eugene Remm"],
    "barre3": ["Sadie Lincoln"],
    "corepower yoga": ["Trevor Tice"],
    "ymca": ["George Williams"],
    "jazzercise": ["Judi Sheppard Missett"],
    "curves": ["Gary Heavin"],
    "kickhouse": ["Sean Eamer"],
}

# Known hallucination names that LLMs commonly produce
KNOWN_HALLUCINATIONS = {
    "mark rober", "elon musk", "jeff bezos", "bill gates", "mark zuckerberg",
    "warren buffett", "steve jobs", "tim cook", "sundar pichai", "satya nadella",
    "sam altman", "john doe", "jane doe", "john smith", "jane smith",
    "bob smith", "test user", "admin", "owner", "founder",
}


def validate_owner_name(name: str, gym_name: str = "", gym_type: str = "") -> tuple[bool, str]:
    """Validate an extracted owner name.

    Returns (is_valid, rejection_reason). If is_valid is True, rejection_reason is empty.
    """
    if not name or not name.strip():
        return False, "empty name"

    name = name.strip()

    # "Unknown" check
    if name.lower() in ("unknown", "n/a", "none", "not found", "not available"):
        return False, "unknown/placeholder"

    # Must have first + last name (2+ words)
    words = name.split()
    if len(words) < 2:
        return False, f"single word name: '{name}'"

    # Reasonable length
    if len(name) < 4 or len(name) > 60:
        return False, f"unreasonable length ({len(name)} chars)"

    # Each word should be 2-20 chars (allow single-char initials like "J.")
    for word in words:
        clean_word = word.rstrip(".")
        if len(clean_word) > 20:
            return False, f"word too long: '{word}'"

    # No numbers
    if re.search(r"\d", name):
        return False, f"contains numbers: '{name}'"

    # No special characters (allow hyphens, apostrophes, periods for names like O'Brien, Mary-Jane, J.)
    if re.search(r"[^a-zA-Z\s\-'.]", name):
        return False, f"contains special characters: '{name}'"

    # Check known hallucinations
    if name.lower() in KNOWN_HALLUCINATIONS:
        return False, f"known hallucination: '{name}'"

    # Check franchise founder blocklist
    gym_lower = (gym_name + " " + gym_type).lower()
    name_lower = name.lower()
    for brand_keyword, founders in KNOWN_FRANCHISE_FOUNDERS.items():
        if brand_keyword in gym_lower:
            for founder in founders:
                if founder.lower() == name_lower:
                    return False, f"franchise founder '{name}' for brand '{brand_keyword}'"

    # Check if name is just the gym name itself
    if gym_name:
        gym_words = set(gym_name.lower().split())
        name_words = set(name.lower().split())
        # If all name words appear in gym name, it's likely the gym name not a person
        if name_words and name_words.issubset(gym_words):
            return False, f"name matches gym name: '{name}'"

    return True, ""

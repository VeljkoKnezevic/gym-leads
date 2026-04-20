"""Detect gym category from website text.

Returns natural labels like "CrossFit gym", "pilates studio", "yoga studio"
suitable for use in cold email copy (e.g., "we help pilates studios...").
"""

from __future__ import annotations

import re

# Each entry: (keywords, label, weight_per_mention)
# Higher weight = fewer mentions needed to win.
# Name-based keywords (brand names) get very high weight since a single
# mention in the gym name is definitive.
_CATEGORIES: list[tuple[list[str], str, int]] = [
    # Brand names — definitive from gym name alone
    (["crossfit"], "CrossFit gym", 50),
    (["orangetheory", "orange theory"], "fitness studio", 50),
    (["f45 training", "f45"], "HIIT studio", 50),

    # Specific disciplines — need meaningful presence in text
    (["pilates", "reformer"], "pilates studio", 10),
    (["yoga"], "yoga studio", 10),
    (["barre"], "barre studio", 10),
    (["cycling", "spin class", "spin studio", "indoor cycling"], "cycling studio", 10),
    (["hyrox"], "functional fitness gym", 10),

    # These can appear as passing mentions on general gym sites,
    # so require heavier presence to win
    (["martial art", "mma", "jiu jitsu", "bjj", "karate", "taekwondo", "judo"], "martial arts gym", 5),
    (["boxing", "kickboxing"], "boxing gym", 5),
    (["hiit", "high intensity", "bootcamp", "boot camp", "circuit training"], "HIIT studio", 5),
    (["personal training", "personal trainer", "1-on-1 training"], "personal training studio", 5),
    (["strength training", "powerlifting", "weightlifting", "barbell", "strongman"], "strength training gym", 5),
    (["swimming", "swim lessons", "aquatic"], "swim studio", 5),
    (["dance", "zumba"], "dance studio", 5),
    (["stretch", "recovery", "cryotherapy"], "recovery studio", 5),
    (["climbing", "bouldering"], "climbing gym", 5),
]

# Fallback — broad gym signals
_GYM_SIGNALS = ["gym", "fitness", "workout", "exercise", "training", "health club"]


def detect_gym_category(text: str, gym_name: str = "") -> str:
    """Detect gym category from website text and gym name.

    Returns a natural label like "CrossFit gym" or "pilates studio".
    Falls back to "gym" if no specific category is detected.
    """
    # Score gym name separately (brand names are definitive)
    name_lower = gym_name.lower()
    text_lower = text.lower()
    text_lower = re.sub(r"\s+", " ", text_lower)

    best_label = ""
    best_score = 0

    for keywords, label, weight in _CATEGORIES:
        score = 0
        for kw in keywords:
            # Name match is very strong signal
            if kw in name_lower:
                score += weight * 10
            # Count occurrences in text body
            score += text_lower.count(kw) * weight

        if score > best_score:
            best_score = score
            best_label = label

    if best_label:
        return best_label

    if any(sig in f"{name_lower} {text_lower}" for sig in _GYM_SIGNALS):
        return "gym"

    return "gym"

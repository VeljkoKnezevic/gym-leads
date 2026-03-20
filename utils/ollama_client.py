"""Thin wrapper around Ollama's local REST API for owner name extraction."""

from __future__ import annotations

import re

import requests

from utils.name_validator import validate_owner_name

_PROMPT_TEMPLATE = """\
You are extracting the owner or founder name of a local gym/fitness studio from its website content.

## Context
- Gym name: {gym_name}
- Gym type: {gym_type}
- Location: {city}, {state}

## Rules
1. Find the LOCAL owner, operator, or founder of THIS specific gym location.
2. For franchise gyms (F45, Orangetheory, Fit Body Boot Camp, CrossFit, etc.), find the LOCAL franchisee or studio owner — NOT the corporate brand founder.
3. Return a full name (first + last). Do NOT return:
   - A single first name only (e.g., "Mike")
   - A trainer, coach, or instructor name (unless they are also the owner)
   - The gym name itself
   - A corporate executive or celebrity
4. If you cannot confidently identify the owner, return "Unknown".

## Examples

Example 1 — Straightforward:
Website says: "Founded by Sarah Chen in 2019, Peak Fitness is a boutique gym..."
[REASONING] The text explicitly states "Founded by Sarah Chen" — she is the founder of this specific gym.
[ANSWER] Sarah Chen

Example 2 — Team page:
Website says: "Our Team: Mike Thompson - Owner & Head Coach, Lisa Park - Lead Trainer..."
[REASONING] Mike Thompson is listed as "Owner & Head Coach" on the team page. Lisa Park is a trainer, not an owner.
[ANSWER] Mike Thompson

Example 3 — Franchise (reject corporate founder):
Gym name: "F45 Training Danbury"
Website mentions: "Rob Deutsch founded F45 in Australia..."
[REASONING] Rob Deutsch is the corporate founder of the F45 brand, not the local franchise owner. No local owner is identified.
[ANSWER] Unknown

Example 4 — Only first name (reject):
Website says: "Coach Mike runs the best workouts in town..."
[REASONING] Only a first name "Mike" is given with no last name, and he's described as a coach, not an owner.
[ANSWER] Unknown

## Instructions
First, write your reasoning in a [REASONING] section analyzing who is mentioned and their roles.
Then, provide your answer in an [ANSWER] section with ONLY the full name or "Unknown".

Website content:
{content}

[REASONING]"""


def find_owner(
    content: str,
    gym_name: str = "",
    gym_type: str = "",
    city: str = "",
    state: str = "",
    model: str = "gpt-oss:20b",
    host: str = "http://localhost:11434",
) -> tuple[str, float]:
    """Ask a local Ollama model to extract the owner/founder name from website text.

    Returns (name, confidence_score). confidence_score is 0.0-1.0.
    Returns ("Unknown", 0.0) if none found, or ("", 0.0) on connection error.
    Never raises.
    """
    if not content.strip():
        return "Unknown", 0.0

    prompt = _PROMPT_TEMPLATE.format(
        content=content,
        gym_name=gym_name or "Unknown",
        gym_type=gym_type or "gym",
        city=city or "Unknown",
        state=state or "",
    )

    try:
        resp = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("response", "").strip()
        name, reasoning = _parse_response(raw)
        name = _clean_name(name)

        if not name or name.lower() == "unknown":
            return "Unknown", 0.0

        # Validate name
        is_valid, reason = validate_owner_name(name, gym_name, gym_type)
        if not is_valid:
            print(f"    [validate] Rejected '{name}': {reason}")
            return "Unknown", 0.0

        # Compute confidence score
        confidence = _compute_confidence(name, reasoning, gym_name, gym_type)
        return name, confidence

    except Exception:
        return "", 0.0


def _parse_response(raw: str) -> tuple[str, str]:
    """Extract the answer and reasoning from structured model output."""
    reasoning = ""
    answer = ""

    # Try to find [ANSWER] marker
    answer_match = re.search(r"\[ANSWER\]\s*(.+?)(?:\n|$)", raw, re.IGNORECASE)
    if answer_match:
        answer = answer_match.group(1).strip()

    # Try to find [REASONING] section
    reasoning_match = re.search(
        r"\[REASONING\]\s*(.*?)(?:\[ANSWER\]|$)", raw, re.IGNORECASE | re.DOTALL
    )
    if reasoning_match:
        reasoning = reasoning_match.group(1).strip()

    # Fallback: if no [ANSWER] marker, use old parsing
    if not answer:
        answer = _clean_name(raw)
        reasoning = raw

    return answer, reasoning


def _clean_name(raw: str) -> str:
    """Normalise model output to just a name or 'Unknown'."""
    if not raw:
        return "Unknown"

    # Take only the first non-empty line (if no [ANSWER] marker was found)
    first_line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), raw.strip())

    # Strip trailing parenthetical notes, e.g. "(Founder & Owner)"
    first_line = re.sub(r"\s*\(.*\)\s*$", "", first_line).strip()

    # Strip quotes
    first_line = first_line.strip("\"'")

    # Strip leading labels like "Answer:" or "Name:"
    first_line = re.sub(r"^(?:answer|name|owner|founder)\s*:\s*", "", first_line, flags=re.IGNORECASE).strip()

    # Any response containing "Unknown" or "unknown" → normalise
    if "unknown" in first_line.lower() or not first_line:
        return "Unknown"

    return first_line


def _compute_confidence(name: str, reasoning: str, gym_name: str, gym_type: str) -> float:
    """Heuristic confidence score based on name quality and reasoning content."""
    score = 0.5
    reasoning_lower = reasoning.lower()

    # Boosts
    if any(kw in reasoning_lower for kw in ("owner", "founded", "founder", "started", "opened", "established")):
        score += 0.2
    if len(name.split()) >= 2:
        score += 0.1
    if any(kw in reasoning_lower for kw in ("explicitly", "clearly", "states", "listed as owner")):
        score += 0.1

    # Penalties
    if any(kw in reasoning_lower for kw in ("unclear", "uncertain", "not sure", "might be", "possibly",
                                             "could be", "no clear", "cannot confirm")):
        score -= 0.2
    if len(name.split()) < 2:
        score -= 0.2
    if re.search(r"\d", name):
        score -= 0.3

    return max(0.0, min(1.0, round(score, 2)))

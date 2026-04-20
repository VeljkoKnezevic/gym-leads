"""Thin wrapper around Ollama's local REST API for owner name extraction."""

from __future__ import annotations

import re

import requests

from utils import llm_cache
from utils.name_validator import validate_owner_name

_PROMPT_TEMPLATE = """\
You are extracting the owner(s) or founder(s) of a local gym/fitness studio from its website content.

## Context
- Gym name: {gym_name}
- Gym type: {gym_type}
- Location: {city}, {state}

## Rules
1. Find ALL LOCAL owners, operators, or founders of THIS specific gym location.
2. For franchise gyms (F45, Orangetheory, Fit Body Boot Camp, CrossFit, etc.), find the LOCAL franchisee or studio owner(s) — NOT the corporate brand founder.
3. Each name must be a full name (first + last). Do NOT include a bare first name, a coach/trainer (unless also an owner), the gym name itself, or a corporate executive.
4. If you cannot confidently identify any owner, return "Unknown".
5. If there are multiple owners or co-founders, list ALL of them — one per line.

## Examples

Example 1 — Single owner:
Website says: "Founded by Sarah Chen in 2019, Peak Fitness is a boutique gym..."
[REASONING] The text explicitly states "Founded by Sarah Chen" — she is the founder.
[ANSWER]
Sarah Chen

Example 2 — Franchise (reject corporate founder):
Gym name: "F45 Training Danbury"
Website mentions: "Rob Deutsch founded F45 in Australia..."
[REASONING] Rob Deutsch is the corporate founder of the F45 brand, not the local franchise owner. No local owner is identified.
[ANSWER]
Unknown

## Instructions
First, write your reasoning in a [REASONING] section analyzing who is mentioned and their roles.
Then, provide your answer in an [ANSWER] section with one full name per line, or "Unknown".

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
    """Ask a local Ollama model to extract owner/founder names from website text.

    Returns (names_string, confidence_score) where names_string may be a
    comma-separated list if multiple owners are found (e.g. "Sarah Chen, Mike Lee").
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

    cached = llm_cache.get(model, prompt)
    if cached is not None:
        return cached

    try:
        resp = requests.post(
            f"{host}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"num_predict": 256},
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("response", "").strip()
        names, reasoning = _parse_response(raw)

        # Validate each name and keep the valid ones
        valid_names = []
        for name in names:
            name = _clean_name(name)
            if not name or name.lower() == "unknown":
                continue
            is_valid, reason = validate_owner_name(name, gym_name, gym_type)
            if not is_valid:
                print(f"    [validate] Rejected '{name}': {reason}")
                continue
            valid_names.append(name)

        if not valid_names:
            llm_cache.put(model, prompt, "Unknown", 0.0)
            return "Unknown", 0.0

        combined = ", ".join(valid_names)
        confidence = _compute_confidence(combined, reasoning, gym_name, gym_type)
        llm_cache.put(model, prompt, combined, confidence)
        return combined, confidence

    except Exception:
        return "", 0.0


def _parse_response(raw: str) -> tuple[list[str], str]:
    """Extract the list of names and reasoning from structured model output.

    Returns (list_of_name_strings, reasoning_string).
    """
    reasoning = ""
    names: list[str] = []

    # Extract [REASONING] section
    reasoning_match = re.search(
        r"\[REASONING\]\s*(.*?)(?:\[ANSWER\]|$)", raw, re.IGNORECASE | re.DOTALL
    )
    if reasoning_match:
        reasoning = reasoning_match.group(1).strip()

    # Extract everything after [ANSWER]
    answer_match = re.search(r"\[ANSWER\]\s*(.*?)$", raw, re.IGNORECASE | re.DOTALL)
    if answer_match:
        answer_block = answer_match.group(1).strip()
        # Each non-empty line is a candidate name
        for line in answer_block.splitlines():
            line = line.strip().strip("-•*").strip()
            if not line:
                continue
            # Split comma-separated names on a single line
            # e.g. "Chris Douglass, Libby Douglass"
            if "," in line and not line.lower().startswith("unknown"):
                for part in line.split(","):
                    part = part.strip()
                    if part:
                        names.append(part)
            else:
                names.append(line)
    else:
        # Fallback: treat the whole response as a single name attempt
        names = [raw.strip()]
        reasoning = raw

    return names, reasoning


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

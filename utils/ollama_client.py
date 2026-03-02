"""Thin wrapper around Ollama's local REST API for owner name extraction."""

from __future__ import annotations

import requests

_PROMPT_TEMPLATE = """\
You are extracting the owner or founder name from gym/fitness studio website content.

Rules:
- Return ONLY the full name (e.g. "Jane Smith") — no extra words, no punctuation.
- If multiple owner/founder names appear, return the primary one.
- If no owner or founder name is found, return exactly: Unknown

Website content:
{content}

Owner/founder full name:"""


def find_owner(
    content: str,
    model: str = "mistral:7b",
    host: str = "http://localhost:11434",
) -> str:
    """Ask a local Ollama model to extract the owner/founder name from website text.

    Returns the name string, "Unknown" if none found, or "" on connection error.
    Never raises.
    """
    if not content.strip():
        return "Unknown"

    prompt = _PROMPT_TEMPLATE.format(content=content)

    try:
        resp = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("response", "").strip()
        return _clean_response(raw)
    except Exception:
        return ""


def _clean_response(raw: str) -> str:
    """Normalise model output to just a name or 'Unknown'."""
    # Take only the first non-empty line
    first_line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), raw.strip())

    # Strip trailing parenthetical notes, e.g. "(Founder & Owner)"
    import re
    first_line = re.sub(r"\s*\(.*\)\s*$", "", first_line).strip()

    # Any response containing "Unknown" or "unknown" → normalise
    if "unknown" in first_line.lower() or not first_line:
        return "Unknown"

    return first_line

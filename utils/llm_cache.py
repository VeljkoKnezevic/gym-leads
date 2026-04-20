"""Disk cache for LLM owner-extraction results.

Keyed on (model + prompt) hash so cache hits are exact. Survives crashes and
re-runs of the enrich pipeline at zero LLM cost.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CACHE_DIR = Path(".cache/llm")


def _key(model: str, prompt: str) -> str:
    h = hashlib.sha1(f"{model}\n{prompt}".encode("utf-8")).hexdigest()
    return h


def _path(model: str, prompt: str) -> Path:
    return CACHE_DIR / f"{_key(model, prompt)}.json"


def get(model: str, prompt: str) -> tuple[str, float] | None:
    """Return (name, confidence) if cached, else None."""
    p = _path(model, prompt)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data["name"], float(data["confidence"])
    except Exception:
        return None


def put(model: str, prompt: str, name: str, confidence: float) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(model, prompt)
    p.write_text(
        json.dumps({"name": name, "confidence": confidence}),
        encoding="utf-8",
    )

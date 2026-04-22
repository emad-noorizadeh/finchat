"""Pattern library — reusable starting skeletons for new sub-agents.

Each pattern is a JSON file in this directory with a `skeleton` field the
frontend clones into the Agent Builder canvas. Pure read-only catalog; the
backend does not interpret patterns at runtime.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DIR = Path(__file__).parent


def list_patterns() -> list[dict]:
    out: list[dict] = []
    for f in sorted(_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception as e:  # noqa: BLE001
            logger.error("[pattern_load_failed] file=%s err=%s", f.name, e)
    return out


def get_pattern(pattern_id: str) -> dict | None:
    for p in list_patterns():
        if p.get("pattern_id") == pattern_id:
            return p
    return None

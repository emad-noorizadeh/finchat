"""Generates `backend/langgraph.json` from the DB-backed template store.

The LangGraph CLI parses `langgraph.json` at startup to learn which graphs
to expose in Studio. To make UI-built sub-agents show up there automatically,
this module rewrites the file whenever the agent set changes — currently:

  * On app startup (after `initialize_templates()` + `init_agents()`).
  * Inside `agents.py:_refresh_registry()`, which already fires on every
    Agent Builder create/update/delete/deploy/disable.

The write is hash-gated: identical content is a no-op so `langgraph dev`'s
file-watcher doesn't reload on every save when nothing meaningful changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# backend/ root (this file lives at backend/app/agent/studio_setup.py).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PATH = _BACKEND_ROOT / "langgraph.json"

_STUDIO_GRAPHS = "./app/agent/studio_graphs.py"


def _build_config(template_names: list[str]) -> dict:
    graphs: dict[str, str] = {
        "planner": f"{_STUDIO_GRAPHS}:planner_graph",
    }
    for name in sorted(template_names):
        graphs[f"agent_{name}"] = f"{_STUDIO_GRAPHS}:agent_{name}"

    return {
        "$schema": "https://langgra.ph/schema.json",
        "_generated_by": "app.agent.studio_setup — do not edit by hand",
        "python_version": "3.13",
        "dependencies": ["."],
        "env": ".env",
        "graphs": graphs,
    }


def _serialise(config: dict) -> str:
    return json.dumps(config, indent=2) + "\n"


def regenerate_langgraph_config(path: Path | None = None) -> bool:
    """Rewrite `langgraph.json` from the current template_store contents.

    Returns True if the file was actually written, False if the content was
    unchanged (no-op write skipped to avoid spurious `langgraph dev` reloads).
    """
    target = path or _DEFAULT_PATH

    from app.agents.template_store import list_templates
    templates = list_templates()
    template_names = [t.name for t in templates]

    config = _build_config(template_names)
    new_text = _serialise(config)
    new_hash = hashlib.sha256(new_text.encode()).hexdigest()

    if target.exists():
        old_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        if old_hash == new_hash:
            return False

    target.write_text(new_text)
    logger.info(
        "[studio_langgraph_regenerated] path=%s graphs=%d",
        target, len(config["graphs"]),
    )
    return True

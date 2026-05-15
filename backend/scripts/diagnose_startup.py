"""Bisect lifespan startup to find where boot hangs or silently exits.

Use this when `python run.py` (or `uvicorn app.main:app`) fails to print
`Application startup complete` and you can't tell which step in the
lifespan is wedged.

It runs each `app/main.py:lifespan` step in order, prints which step
started and which finished, and flushes between each so you see exactly
where it stalls. No uvicorn / reloader layer, so:

  - A silent `sys.exit()` shows as "stopped after step N OK".
  - A genuine hang lets you Ctrl-C and see a real Python traceback
    pointing at the exact line.

Usage:
  cd backend
  source .venv/bin/activate
  python scripts/diagnose_startup.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def _hdr(n: int, total: int, label: str) -> None:
    print(f"[{n}/{total}] {label} ...", flush=True)


def _ok() -> None:
    print("       OK", flush=True)


def main() -> int:
    total = 8

    _hdr(1, total, "configure_langsmith")
    from app.observability import configure_langsmith
    configure_langsmith()
    _ok()

    _hdr(2, total, "LLM startup check")
    from app.config import settings
    if settings.llm_startup_check:
        from app.services.llm_service import startup_check
        asyncio.run(startup_check())
    else:
        print("       skipped (LLM_STARTUP_CHECK=false)", flush=True)
    _ok()

    _hdr(3, total, "run_migrations")
    from app.database import run_migrations
    run_migrations()
    _ok()

    _hdr(4, total, "init_tools")
    from app.tools import init_tools
    init_tools()
    _ok()

    _hdr(5, total, "initialize_templates")
    from app.agents.templates import initialize_templates
    initialize_templates()
    _ok()

    _hdr(6, total, "init_agents")
    from app.agents import init_agents
    init_agents()
    _ok()

    _hdr(7, total, "regenerate_langgraph_config")
    from app.agent.studio_setup import regenerate_langgraph_config
    regenerate_langgraph_config()
    _ok()

    _hdr(8, total, "KB descriptor bootstrap")
    descriptor_path = Path(settings.kb_descriptor_path)
    if not descriptor_path.exists():
        from app.services.rag_service import RAGService
        from app.database import get_chroma_client
        RAGService(get_chroma_client()).rebuild_kb_descriptor()
    else:
        print(f"       skipped (already exists at {descriptor_path})", flush=True)
    _ok()

    print()
    print("ALL LIFESPAN STEPS COMPLETE", flush=True)
    print(
        "If this script succeeds but `python run.py` still fails, the "
        "issue is in uvicorn / asyncio wiring, not in app code.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

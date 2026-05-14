"""Factory wrappers exposed to LangGraph Studio via `langgraph.json`.

Two entry points:

  * `planner_graph` — the top-level Planner + deterministic-Presenter graph.
  * `agent_<template_name>` — one factory per sub-agent template. Reads
    from the same DB-backed `template_store` the runtime uses, so what
    Studio shows is exactly what the app runs (including templates
    created/edited in the Agent Builder UI).

The factories are materialised into `globals()` at import time by querying
the DB. We can't use a module-level `__getattr__` here because
`langgraph-api` resolves graph references via direct `module.__dict__[name]`
access, which bypasses `__getattr__`. Reloads on file change (triggered by
`watchfiles` when `langgraph.json` is rewritten) re-execute this block and
pick up newly-created agents.

`_bootstrap_app_context()` mirrors the FastAPI lifespan startup so the
planner running inside Studio sees the same registered tools, templates,
and observability config as the real backend. Without this, the planner
would discover zero tools and most tool calls would no-op.

The CLI passes a RunnableConfig positional to each factory; we accept and
ignore it (the underlying compilers take no runtime config).

== Running a meaningful turn in Studio ==

Click a graph in Studio's sidebar, then in "New Run" paste a state dict
shaped like the one `app/routers/chat.py:303` builds for a real chat:

    {
      "messages": [{"role": "user", "content": "show my transactions"}],
      "user_id": "<a real user_id from chat_sessions.user_id in app.db>",
      "session_id": "studio-debug-001",
      "available_tools": [],
      "tool_schemas": [],
      "iteration_count": 0,
      "enrichment_context": "",
      "base_system_prompt": "",
      "knowledge_sources": [],
      "search_tool_calls": 0,
      "channel": "chat",
      "response_terminated": false,
      "last_executed_tools": [],
      "variables": {},
      "hop_guard_triggered": false
    }

Replace `user_id` with one that exists in your DB so `enrich()` can
rehydrate profile/transactions. With this state, the planner executes
end-to-end including tool calls and sub-agent invocations, and every
node + LLM + tool span lands in your LangSmith `finchat` project.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.agent.graph import build_agent_graph
from app.agents.template_compiler import compile_template
from app.agents.template_store import _load_row, get_row, list_templates

logger = logging.getLogger(__name__)

_AGENT_PREFIX = "agent_"
_bootstrapped = False


def _bootstrap_app_context() -> None:
    """Mirror the FastAPI lifespan so the planner has tools, templates, and
    observability wired when invoked from Studio. Idempotent — guarded by
    the module-level `_bootstrapped` flag so `watchfiles` reloads don't
    re-import-storm tool modules."""
    global _bootstrapped
    if _bootstrapped:
        return

    # 1. LangSmith env — so Studio runs also land in the `finchat` project.
    try:
        from app.observability import configure_langsmith
        configure_langsmith()
    except Exception:
        logger.warning("studio bootstrap: configure_langsmith failed", exc_info=True)

    # 2. Tool registry — side-effecting imports populate _REGISTRY.
    try:
        from app.tools import init_tools
        init_tools()
    except Exception:
        logger.warning("studio bootstrap: init_tools failed", exc_info=True)

    # 3. Sub-agent templates + (agent_name, channel) lookup.
    try:
        from app.agents.templates import initialize_templates
        initialize_templates()
        from app.agents import init_agents
        init_agents()
    except Exception:
        logger.warning("studio bootstrap: agents init failed", exc_info=True)

    _bootstrapped = True
    logger.info("studio bootstrap complete — tools + templates + LangSmith ready")


_bootstrap_app_context()


def planner_graph(_config: Any = None):
    """Top-level Planner + deterministic-Presenter StateGraph."""
    return build_agent_graph(checkpointer=None)


def _make_agent_factory(template_name: str) -> Callable:
    def factory(_config: Any = None):
        row = get_row(template_name)
        if row is None:
            raise RuntimeError(
                f"sub-agent template {template_name!r} not found in DB — "
                f"it may have been deleted since langgraph.json was generated"
            )
        template = _load_row(row)
        if template is None:
            raise RuntimeError(
                f"sub-agent template {template_name!r} failed to load "
                f"(see [template_row_invalid] logs)"
            )
        return compile_template(template, checkpointer=None)

    factory.__name__ = f"{_AGENT_PREFIX}{template_name}"
    return factory


def _register_agent_factories() -> None:
    """Materialise one `agent_<name>` factory per DB template into module
    globals so `module.__dict__[name]` lookups (used by langgraph-api)
    succeed. Failures are logged, not raised — a single bad template
    shouldn't prevent Studio from loading the rest."""
    try:
        templates = list_templates()
    except Exception:
        logger.warning(
            "studio_graphs: failed to enumerate templates from DB; only "
            "planner_graph will be exposed. Restart langgraph dev after "
            "the DB is reachable.",
            exc_info=True,
        )
        return

    for template in templates:
        symbol = f"{_AGENT_PREFIX}{template.name}"
        globals()[symbol] = _make_agent_factory(template.name)


_register_agent_factories()

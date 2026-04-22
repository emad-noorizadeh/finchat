"""DynamicSubAgentTool — Planner-callable entry for DB-backed sub-agents.

Every non-regulated `deployed` `SubAgentTemplate` row becomes an instance of
this class at app startup (and on deploy / disable API calls). Mirrors the
shape of TransferAgentTool / RefundAgentTool:

  * The Planner LLM can discover it via `tool_search` (deferred by default
    to keep the always-loaded catalogue small).
  * On invocation, it compiles the agent's channel-specific template and
    drives the inner graph with the same outer-interrupt + accumulated
    state pattern used by the hand-coded sub-agent tools.
  * Every inner invocation carries a LangSmith `trace_config` so the trace
    tree on the dashboard tags the span as this agent, with the caller's
    `user_id` / `session_id` / `channel` in metadata.

Regulated templates (those with `locked_for_business_user_edit=True`) are
SKIPPED by `register_dynamic_sub_agent_tools()` — they ship hand-coded
entry tools (TransferAgentTool / RefundAgentTool) with the right workflow
instructions and response_instructions. Auto-registering them would
duplicate the registry.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from app.agents import template_for_agent
from app.agents.nodes.interrupt_node import apply_resume_escape
from app.agents.runtime import (
    clear_inner_state,
    load_inner_state,
    reset_active_thread,
    save_inner_state,
    set_active_thread,
)
from app.agents.template_compiler import compile_template
from app.tools import _REGISTRY, register_tool
from app.tools.base import BaseTool, ToolErrorCategory, ToolResult
from app.widgets.summarizers import widget_to_llm

logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _compiled_for(agent_name: str, channel: str):
    template = template_for_agent(agent_name, channel)
    if template is None:
        return None, None
    graph = compile_template(template, checkpointer=None)
    return template, graph


class DynamicSubAgentTool(BaseTool):
    """Instances are created per agent_name. Attributes are populated at
    __init__ time from the SubAgentTemplate row(s) — this keeps the class
    generic and avoids a factory/reflection dance."""

    # Set at __init__; BaseTool class attrs are redeclared for clarity.
    name: str = ""
    always_load: bool = False
    should_defer: bool = True           # discover via tool_search
    is_read_only: bool = False
    is_concurrency_safe: bool = False   # stateful driver loop
    channels: tuple = ("chat", "voice")
    has_glass: bool = True

    def __init__(
        self,
        *,
        agent_name: str,
        display_name: str,
        description: str,
        search_hint: str,
        supported_channels: list[str],
    ):
        self.name = agent_name
        self.display_name = display_name
        self._description = description or (
            f"Sub-agent '{display_name or agent_name}'. Call to invoke its flow."
        )
        self.search_hint = search_hint or agent_name.replace("_", " ")
        # Honour the template-declared supported channels.
        self.channels = tuple(supported_channels or ("chat", "voice"))
        self.has_glass = "voice" in self.channels

    async def description(self, context=None):
        return self._description

    async def input_schema(self):
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The user's request in natural language.",
                },
            },
            "required": ["message"],
        }

    def activity_description(self, input):
        return f"Running {self.display_name or self.name}..."

    async def execute(self, input: dict, context: dict):
        from app.observability import trace_config

        channel = context.get("channel", "chat")
        user_id = context.get("user_id", "")
        session_id = context.get("session_id", "")
        message = (input or {}).get("message", "") or ""

        template, graph = _compiled_for(self.name, channel)
        if graph is None:
            return ToolResult(
                to_llm=f"{self.display_name or self.name} isn't available on this channel.",
                error=f"no template for agent={self.name!r} channel={channel!r}",
                error_category=ToolErrorCategory.SYSTEM,
                user_facing_message=(
                    template.unsupported_channel_message if template is not None else
                    "This agent isn't available on the current channel."
                ),
            )

        thread_id = f"{session_id}_{self.name}_{channel}"
        token = set_active_thread(thread_id)

        inner_config = trace_config(
            run_name=f"{self.name}.{channel}",
            tags=[channel, f"agent:{self.name}", f"user:{user_id}"],
            metadata={
                "agent_name": self.name,
                "channel":    channel,
                "user_id":    user_id,
                "session_id": session_id,
                "source":     "dynamic",
            },
        )

        try:
            inner_state = self._initial_inner_state(
                thread_id=thread_id,
                user_id=user_id,
                session_id=session_id,
                channel=channel,
                message=message,
            )
            inner_state = await _run_inner_once(graph, inner_state, inner_config)

            while _has_pending_interrupt(inner_state):
                payload, inner_state = _consume_pending(inner_state)
                save_inner_state(thread_id, inner_state)

                resume_value = interrupt(payload)
                user_text = _coerce_user_text(resume_value)

                inner_state = load_inner_state(thread_id) or inner_state
                inner_state["messages"] = list(inner_state.get("messages") or []) + [
                    HumanMessage(content=user_text)
                ]
                escape_update = apply_resume_escape(inner_state, user_text)
                if escape_update.get("variables"):
                    inner_state["variables"] = escape_update["variables"]

                inner_state = await _run_inner_once(graph, inner_state, inner_config)

            save_inner_state(thread_id, inner_state)
            result = _terminal_to_tool_result(inner_state, channel=channel)
            clear_inner_state(thread_id)
            return result
        finally:
            reset_active_thread(token)

    def _initial_inner_state(
        self, *, thread_id: str, user_id: str, session_id: str, channel: str, message: str
    ) -> dict:
        prior = load_inner_state(thread_id)
        if prior and not prior.get("_terminal"):
            msgs = list(prior.get("messages") or [])
            msgs.append(HumanMessage(content=message))
            return {**prior, "messages": msgs, "_terminal": False}
        return {
            "messages": [HumanMessage(content=message)],
            "user_id": user_id,
            "session_id": session_id,
            "channel": channel,
            "main_context": {"agent_name": self.name},
            "variables": {},
            "_terminal": False,
        }


# --- helpers (factored out of the class for symmetry with other sub-agent tools) ---


async def _run_inner_once(graph, state: dict, config: dict | None = None) -> dict:
    return await graph.ainvoke(state, config=config) if config else await graph.ainvoke(state)


def _has_pending_interrupt(state: dict) -> bool:
    return bool((state.get("variables") or {}).get("_pending_interrupt_payload"))


def _consume_pending(state: dict) -> tuple[dict, dict]:
    variables = dict(state.get("variables") or {})
    payload = variables.pop("_pending_interrupt_payload", {}) or {}
    return payload, {**state, "variables": variables}


def _coerce_user_text(resume_value) -> str:
    if isinstance(resume_value, dict):
        return str(resume_value.get("utterance", "") or resume_value.get("text", ""))
    return str(resume_value or "")


def _terminal_to_tool_result(state: dict, *, channel: str) -> ToolResult:
    variables = state.get("variables") or {}
    return_mode = variables.get("_return_mode")
    escape_kind = variables.get("_escape_kind")

    if return_mode == "widget":
        widget = variables.get("_response_widget") or {}
        if channel == "voice":
            glass = widget_to_llm(widget)
            return ToolResult(glass=glass, final=True, to_llm=glass)
        return ToolResult(widget=widget, to_llm=widget_to_llm(widget))

    if return_mode == "glass":
        glass = variables.get("_response_glass") or ""
        return ToolResult(glass=glass, final=True, to_llm=glass)

    if return_mode == "to_presenter":
        import json
        writes = variables.get("_response_slot_writes") or {}
        return ToolResult(
            to_llm=json.dumps({"sub_agent_outputs": writes}),
            slot_data=writes,
            go_to_presenter=True,
        )

    if return_mode == "to_orchestrator":
        text = variables.get("_response_text") or ""
        return ToolResult(to_llm=text)

    # Degenerate terminal.
    if escape_kind == "abort":
        return _safe_text("Okay, leaving that.", channel)
    if escape_kind == "topic_change":
        return _safe_text("Okay — what would you like to do instead?", channel)
    return _safe_text("I couldn't complete that request.", channel)


def _safe_text(msg: str, channel: str) -> ToolResult:
    if channel == "voice":
        return ToolResult(glass=msg, final=True, to_llm=msg)
    return ToolResult(to_llm=msg)


# --- Registry management ---


# Track names we've auto-registered so refresh can cleanly add/remove them.
_DYNAMIC_REGISTERED: set[str] = set()


def refresh_dynamic_sub_agent_tools() -> None:
    """Rebuild the dynamic sub-agent tool registry from the DB. Safe to call
    on every deploy / disable event.

    Rules:
      - Only `deployed` rows are registered.
      - Only NON-regulated rows are registered (regulated flows ship
        hand-coded tools with richer instructions).
      - If a hand-coded tool with the same `agent_name` already exists,
        skip auto-registration to avoid duplicates.
    """
    from app.agents.template_store import list_rows_all

    # Group rows by agent_name so we register one tool per agent (all
    # channels served by the same instance). Prefer the row carrying the
    # richest description/search_hint.
    by_agent: dict[str, dict] = {}
    for row in list_rows_all():
        if row.status != "deployed":
            continue
        if row.locked_for_business_user_edit:
            continue
        agent_name = row.agent_name or row.name
        acc = by_agent.setdefault(agent_name, {
            "display_name":      row.display_name,
            "description":       row.description,
            "search_hint":       row.search_hint,
            "supported_channels": set(),
        })
        acc["supported_channels"].update(row.supported_channels or [row.channel])
        # Keep the longest non-empty description / search_hint across
        # channel variants.
        if row.description and len(row.description) > len(acc["description"] or ""):
            acc["description"] = row.description
        if row.search_hint and len(row.search_hint) > len(acc["search_hint"] or ""):
            acc["search_hint"] = row.search_hint
        if row.display_name and not acc["display_name"]:
            acc["display_name"] = row.display_name

    # Remove stale dynamic registrations (agents that used to exist but
    # were deleted / disabled since last refresh).
    for stale in _DYNAMIC_REGISTERED - set(by_agent):
        _REGISTRY.pop(stale, None)
    _DYNAMIC_REGISTERED.intersection_update(by_agent)

    # Register (or re-register with possibly updated metadata) each agent.
    for agent_name, meta in by_agent.items():
        if agent_name in _REGISTRY and agent_name not in _DYNAMIC_REGISTERED:
            # Hand-coded tool already wins.
            logger.debug("[dynamic_sub_agent_skip] %s (hand-coded tool exists)", agent_name)
            continue
        tool = DynamicSubAgentTool(
            agent_name=agent_name,
            display_name=meta["display_name"] or agent_name,
            description=meta["description"],
            search_hint=meta["search_hint"],
            supported_channels=sorted(meta["supported_channels"]),
        )
        register_tool(tool)
        _DYNAMIC_REGISTERED.add(agent_name)
        logger.info(
            "[dynamic_sub_agent_registered] agent=%s channels=%s",
            agent_name, sorted(meta["supported_channels"]),
        )

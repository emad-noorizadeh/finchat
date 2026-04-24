"""TransferAgentTool — drives the compiled Transfer sub-agent StateGraph.

Each channel (chat, voice) ships its own LoadedTemplate (chat is widget-first,
voice is interrupt-heavy). `execute` picks the template for the current
channel, compiles it once per process (cached), and drives it via the outer
graph's interrupt-replay mechanism.

Driver shape:
  - The inner graph compiles WITHOUT a checkpointer. When an interrupt_node
    runs, it sets a state flag and the compiler routes that node to END —
    the inner graph terminates cleanly on every pause.
  - Accumulated inner state (variables + messages + retry tracking) is
    stored in a module-level dict keyed by thread_id across outer pauses.
  - On outer replay: this execute() re-runs from the top; calls to outer
    interrupt() replay cached resume values in order. Each iteration of the
    driver loop pops one resume value (user's reply), appends it as a
    HumanMessage to accumulated messages, re-runs the inner graph, and
    either pauses again (next outer interrupt) or terminates.

State isolation — the sub-agent's state is separate from the parent. The
parent only observes this tool's ToolResult (mapped from the terminal
response_node).
"""

from __future__ import annotations

import json
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
from app.tools import register_tool
from app.tools.base import BaseTool, ToolErrorCategory, ToolResult
from app.widgets.summarizers import widget_to_llm

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def _compiled_for(agent_name: str, channel: str):
    template = template_for_agent(agent_name, channel)
    if template is None:
        return None, None
    graph = compile_template(template, checkpointer=None)
    return template, graph


class TransferAgentTool(BaseTool):
    name = "transfer_money"
    always_load = True
    should_defer = False
    search_hint = "transfer money send pay zelle credit card between accounts"
    is_read_only = False
    is_concurrency_safe = False
    widget = "transfer_form"
    channels = ("chat", "voice")
    has_glass = True
    flow = (
        "Load the channel-appropriate sub-agent template (chat | voice)",
        "Compile to a LangGraph StateGraph on first use (cached)",
        "Drive the inner graph iteratively; pause via outer interrupt()",
        "Map the terminal response_node's return_mode onto a ToolResult",
    )
    errors = (
        "No template registered for channel → SYSTEM error, safe user message",
        "tool_call_node sub-tool failure → sub-agent routes to response_failed → to_orchestrator text",
        "Outer interrupt pauses outer execution until user replies",
    )
    response_instructions = (
        "The transfer sub-agent returned. If it emitted a widget, that's "
        "on screen — acknowledge briefly. If it returned a glass / user-facing "
        "text, speak it verbatim without restating amounts or account ids."
    )

    async def description(self, context=None):
        return (
            "Initiate a money transfer. Supports THREE transfer types — the "
            "sub-agent picks the right one from the user's wording:\n"
            "  • m2m   — between the user's OWN accounts (e.g. checking → savings)\n"
            "  • zelle — to a person (friend, family) by name or contact\n"
            "  • cc    — to pay an external credit-card account\n\n"
            "Multi-step flow: detects type, collects amount + source + destination "
            "(or payee for Zelle), confirms, executes. Pass the user's full request "
            "verbatim as `message` — the sub-agent parses type, amount, and accounts.\n\n"
            "When to call:\n"
            "- Any phrase about moving money (\"transfer\", \"send\", \"move\", \"pay\")\n"
            "- Any destination — the user's own account, another person, or an "
            "  external credit card\n"
            "- DO NOT decline person-to-person requests; route them — the sub-agent "
            "  handles Zelle.\n\n"
            "Do NOT pre-fill account or payee details — the sub-agent collects them."
        )

    async def input_schema(self):
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "User's transfer request in natural language",
                },
            },
            "required": ["message"],
        }

    def activity_description(self, input):
        return "Processing transfer..."

    async def execute(self, input: dict, context: dict):
        channel = context.get("channel", "chat")
        user_id = context.get("user_id", "")
        session_id = context.get("session_id", "")
        message = (input or {}).get("message", "") or ""

        template, graph = _compiled_for(self.name, channel)
        if graph is None:
            return ToolResult(
                to_llm="Transfer sub-agent is not configured for this channel.",
                error=f"no template for agent={self.name!r} channel={channel!r}",
                error_category=ToolErrorCategory.SYSTEM,
                user_facing_message=(
                    "Transfers aren't available here. Please try the app or chat."
                ),
            )

        thread_id = f"{session_id}_{self.name}_{channel}"
        token = set_active_thread(thread_id)

        # Trace config — tags this sub-agent invocation as a child span in
        # LangSmith with filterable user/session/channel metadata.
        from app.observability import trace_config
        inner_config = trace_config(
            run_name=f"{self.name}.{channel}",
            tags=[channel, f"agent:{self.name}", f"user:{user_id}"],
            metadata={
                "agent_name": self.name,
                "channel":    channel,
                "user_id":    user_id,
                "session_id": session_id,
            },
        )

        try:
            inner_state = _initial_inner_state(
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

                # Outer interrupt — replays cached resume value on outer
                # re-entry; raises GraphInterrupt on first encounter.
                resume_value = interrupt(payload)
                user_text = _coerce_user_text(resume_value)

                # Reload in case the process was restarted between turns
                # (best-effort — with MemorySaver, accumulated state is
                # per-process; a restart loses context and the driver starts
                # fresh with only the new user_text).
                inner_state = load_inner_state(thread_id) or inner_state
                inner_state["messages"] = list(inner_state.get("messages") or []) + [
                    HumanMessage(content=user_text)
                ]
                escape_update = apply_resume_escape(inner_state, user_text)
                if escape_update.get("variables"):
                    inner_state["variables"] = escape_update["variables"]

                inner_state = await _run_inner_once(graph, inner_state, inner_config)

            # Terminal — extract ToolResult.
            save_inner_state(thread_id, inner_state)
            result = _terminal_to_tool_result(inner_state, channel=channel)
            clear_inner_state(thread_id)
            return result

        finally:
            reset_active_thread(token)


def _initial_inner_state(
    *, thread_id: str, user_id: str, session_id: str, channel: str, message: str
) -> dict:
    """Build the inner state for a fresh invocation. If accumulated state
    exists for this thread (shouldn't — terminal clears it — but guards
    against a leak), merge the new message into it."""
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
        # Expose agent_name to tool_call_node so it can resolve
        # per-agent AgentTool registrations.
        "main_context": {"agent_name": "transfer_money"},
        "variables": {},
        "_terminal": False,
    }


async def _run_inner_once(graph, state: dict, config: dict | None = None) -> dict:
    """Invoke the inner graph once. Returns the merged final state. The
    optional `config` carries LangSmith trace tags/metadata; harmless when
    tracing is disabled."""
    return await graph.ainvoke(state, config=config) if config else await graph.ainvoke(state)


def _has_pending_interrupt(state: dict) -> bool:
    vars_ = state.get("variables") or {}
    return bool(vars_.get("_pending_interrupt_payload"))


def _consume_pending(state: dict) -> tuple[dict, dict]:
    """Pop the pending-interrupt payload from state. Returns (payload, state')."""
    variables = dict(state.get("variables") or {})
    payload = variables.pop("_pending_interrupt_payload", {}) or {}
    new_state = {**state, "variables": variables}
    return payload, new_state


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
        writes = variables.get("_response_slot_writes") or {}
        return ToolResult(
            to_llm=json.dumps({"sub_agent_outputs": writes}),
            slot_data=writes,
            go_to_presenter=True,
        )

    if return_mode == "to_orchestrator":
        text = variables.get("_response_text") or ""
        return ToolResult(to_llm=text)

    # Degenerate terminal — no return_mode set.
    if escape_kind == "abort":
        return _safe_text("Okay, leaving that.", channel)
    if escape_kind == "topic_change":
        return _safe_text(
            "Okay — what would you like to do instead?",
            channel,
        )
    return _safe_text("I couldn't complete that request.", channel)


def _safe_text(msg: str, channel: str) -> ToolResult:
    if channel == "voice":
        return ToolResult(glass=msg, final=True, to_llm=msg)
    return ToolResult(to_llm=msg)


register_tool(TransferAgentTool())

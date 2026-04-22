"""RefundAgentTool — Planner-callable entry point for the Fee Refund flow.

Mirrors TransferAgentTool. Loads the `refund_fee` template per channel,
compiles it to a LangGraph StateGraph (cached), and drives the inner graph
using the same outer-interrupt + accumulated-inner-state pattern. The
terminal response_node's return_mode → ToolResult (widget / glass / text).
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
from app.agents.state import SubAgentState
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


class RefundAgentTool(BaseTool):
    name = "refund_fee"
    always_load = True
    should_defer = False
    search_hint = "fee refund late fee cash advance credit card refund reimburse"
    is_read_only = False
    is_concurrency_safe = False
    widget = "refund_form"
    channels = ("chat", "voice")
    has_glass = True
    flow = (
        "Load the channel-appropriate sub-agent template (chat | voice)",
        "Compile to a LangGraph StateGraph on first use (cached)",
        "Drive the inner graph; chat terminates at a refund_form widget, voice via interrupts",
        "Map the terminal response_node's return_mode onto a ToolResult",
    )
    errors = (
        "No template registered for channel → SYSTEM error, safe user message",
        "No fees eligible → response_no_fees (to_orchestrator text)",
        "Sub-tool failure → ERROR dict routed to response_failed",
    )
    response_instructions = (
        "The fee-refund sub-agent returned. If it emitted a widget, that's on "
        "screen — acknowledge briefly. If it returned a glass / user-facing "
        "text, speak it verbatim without restating account numbers."
    )

    async def description(self, context=None):
        return (
            "Refund a fee charged to the user's credit card (late fees, cash-advance "
            "interest, etc.). Multi-step flow: lists eligible fees, lets the user "
            "pick one, submits for evaluation, returns APPROVED or DENIED.\n\n"
            "Examples:\n"
            "- \"Can I get my late fee refunded?\" → refund_fee(message=\"late fee refund\")\n"
            "- \"Refund my cash advance interest\" → refund_fee(message=\"refund cash advance interest\")\n"
            "- \"I want a refund on a fee\" → refund_fee(message=\"fee refund\")"
        )

    async def input_schema(self):
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "User's refund request in natural language",
                },
            },
            "required": ["message"],
        }

    def activity_description(self, input):
        return "Reviewing eligible fees..."

    async def execute(self, input: dict, context: dict):
        channel = context.get("channel", "chat")
        user_id = context.get("user_id", "")
        session_id = context.get("session_id", "")
        message = (input or {}).get("message", "") or ""

        template, graph = _compiled_for(self.name, channel)
        if graph is None:
            return ToolResult(
                to_llm="Fee-refund sub-agent is not configured for this channel.",
                error=f"no template for agent={self.name!r} channel={channel!r}",
                error_category=ToolErrorCategory.SYSTEM,
                user_facing_message="Fee refunds aren't available here.",
            )

        thread_id = f"{session_id}_{self.name}_{channel}"
        token = set_active_thread(thread_id)

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
    *, thread_id: str, user_id: str, session_id: str, channel: str, message: str
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
        "main_context": {"agent_name": "refund_fee"},
        "variables": {},
        "_terminal": False,
    }


async def _run_inner_once(graph, state: dict, config: dict | None = None) -> dict:
    """Invoke the inner graph once with an optional LangSmith trace config.
    The trace fields are no-ops when LangSmith tracing is disabled."""
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

    if return_mode == "to_orchestrator":
        text = variables.get("_response_text") or ""
        return ToolResult(to_llm=text)

    if escape_kind == "abort":
        return _safe_text("Okay, leaving that.", channel)
    if escape_kind == "topic_change":
        return _safe_text("Okay — what would you like to do instead?", channel)
    return _safe_text("I couldn't complete that request.", channel)


def _safe_text(msg: str, channel: str) -> ToolResult:
    if channel == "voice":
        return ToolResult(glass=msg, final=True, to_llm=msg)
    return ToolResult(to_llm=msg)


register_tool(RefundAgentTool())

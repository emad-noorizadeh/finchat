"""tool_node — runs tool_calls emitted by the previous llm_node.

Standard LangGraph pattern. Runs every tool call in the latest AIMessage
via the sub-agent's tool_caller, appends ToolMessages. Does NOT terminate.

For sub-agents using llm_node + tool_node. Transfer doesn't use this (it
uses explicit tool_call_node for every tool invocation).

Data schema:
  (no fields — reads from state.messages[-1].tool_calls)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from langchain_core.messages import AIMessage, ToolMessage

from app.agents.nodes import register_node_type

logger = logging.getLogger(__name__)


def build_tool_node_factory(data: dict) -> Callable:
    async def handler(state: dict) -> dict:
        tool_caller = state.get("_tool_caller")
        last = (state.get("messages") or [])[-1] if state.get("messages") else None
        if not isinstance(last, AIMessage) or not getattr(last, "tool_calls", None):
            return {}

        # Run every tool_call concurrently. Each per-call try/except below
        # converts exceptions into an error-shaped ToolMessage so a single
        # failure can't abort the gather. Order is preserved because
        # asyncio.gather returns results in the same order as inputs, which
        # keeps tool_call_id pairings stable for the LLM's next turn.
        async def _run_one(tc):
            try:
                if tool_caller is not None:
                    result = await tool_caller(
                        tool_name=tc["name"],
                        action=tc.get("args", {}).get("action"),
                        params=tc.get("args", {}),
                        state=state,
                    )
                else:
                    result = await _default_tool_caller(
                        tool_name=tc["name"],
                        args=tc.get("args") or {},
                        state=state,
                    )
                return _to_str(result)
            except Exception as e:  # noqa: BLE001
                return json.dumps({"error": str(e)})

        contents = await asyncio.gather(*(_run_one(tc) for tc in last.tool_calls))
        tool_messages = [
            ToolMessage(content=content, tool_call_id=tc["id"])
            for content, tc in zip(contents, last.tool_calls)
        ]

        logger.info("[subagent_tool_node.v1] ran=%d parallel=true", len(tool_messages))
        return {"messages": tool_messages}

    return handler


async def _default_tool_caller(*, tool_name: str, args: dict, state: dict):
    """Fallback used when no per-thread tool_caller was registered (the
    Dynamic sub-agent path). Looks the tool up in the global registry and
    dispatches via BaseTool.execute / AgentTool.dispatch as appropriate."""
    from app.tools import get_tool
    from app.tools.agent_tool import AgentTool

    tool = get_tool(tool_name)
    if tool is None:
        return {"error": f"unknown tool {tool_name!r}"}

    context = {
        "user_id":    state.get("user_id", ""),
        "session_id": state.get("session_id", ""),
        "channel":    state.get("channel", ""),
    }

    if isinstance(tool, AgentTool):
        action = (args or {}).get("action") or ""
        params = {k: v for k, v in (args or {}).items() if k != "action"}
        return await tool.dispatch(action, params, context)

    return await tool.execute(args or {}, context)


def _to_str(result) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if hasattr(result, "to_llm"):
        return result.to_llm or ""
    if isinstance(result, dict):
        return json.dumps(result, default=str)
    return str(result)


register_node_type("tool_node", build_tool_node_factory)

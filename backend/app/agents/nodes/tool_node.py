"""tool_node — runs tool_calls emitted by the previous llm_node.

Standard LangGraph pattern. Runs every tool call in the latest AIMessage
via the sub-agent's tool_caller, appends ToolMessages. Does NOT terminate.

For sub-agents using llm_node + tool_node. Transfer doesn't use this (it
uses explicit tool_call_node for every tool invocation).

Data schema:
  (no fields — reads from state.messages[-1].tool_calls)
"""

from __future__ import annotations

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
        if tool_caller is None:
            return {}

        tool_messages = []
        for tc in last.tool_calls:
            try:
                result = await tool_caller(
                    tool_name=tc["name"],
                    action=tc.get("args", {}).get("action"),
                    params=tc.get("args", {}),
                    state=state,
                )
                content = _to_str(result)
            except Exception as e:  # noqa: BLE001
                content = json.dumps({"error": str(e)})
            tool_messages.append(
                ToolMessage(content=content, tool_call_id=tc["id"]),
            )

        logger.info("[subagent_tool_node.v1] ran=%d", len(tool_messages))
        return {"messages": tool_messages}

    return handler


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

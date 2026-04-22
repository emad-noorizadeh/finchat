"""tool_call_node — dispatch a declared AgentTool action.

The template names a tool + action; the dispatcher looks up the tool in
AGENT_TOOL_REGISTRY (agent-scoped first, then global fallback) and calls
`tool.dispatch(action, params, context)`. Params are templated
(`{{variables.X}}`) before dispatch.

post_write: flat dict of `{variable: value}` applied to `state.variables`
ONLY on tool success. Used to clear stale flags (e.g.
`acknowledged_validation_failure` after a successful re-validate).

Data schema:
  tool: str                            # AgentTool.name
  action: str                          # AgentTool action — required
  params: dict                         # templated from state.variables
  output_var: str                      # required — where to write the result
  post_write: dict | null              # optional flat state resets on success
  on_error: "abort" | "<node_id>"      # default: abort (unused — reserved)

Legacy shim: a few older templates pass `tool` as a bare dispatch name
(e.g. "transfer_details") with no action. The dispatcher detects that and
maps them through the `_LEGACY_ALIASES` table to the new (tool, action)
pair so those templates keep running during the migration.
"""

from __future__ import annotations

import logging
from typing import Callable

from app.agents.nodes import register_node_type

logger = logging.getLogger(__name__)


# name-only shortcuts from old templates/closures → (tool, action).
_LEGACY_ALIASES: dict[str, tuple[str, str]] = {
    "transfer_details": ("transfer", "get_details"),
    "transfer_validate": ("transfer", "validate"),
    "transfer_submit": ("transfer", "submit"),
    "resolve_account": ("transfer", "resolve_account"),
}


def build_tool_call_node_factory(data: dict) -> Callable:
    tool_name = data.get("tool") or ""
    action_name = data.get("action")
    params_template = data.get("params") or {}
    output_var = data.get("output_var") or ""
    post_write = data.get("post_write") or {}

    if not tool_name:
        raise ValueError("tool_call_node.data.tool is required")
    if not output_var:
        raise ValueError("tool_call_node.data.output_var is required")
    if not isinstance(post_write, dict):
        raise ValueError("tool_call_node.data.post_write must be a flat dict")

    # Resolve legacy "tool-as-action" aliases once at compile time.
    effective_tool = tool_name
    effective_action = action_name
    if not effective_action and tool_name in _LEGACY_ALIASES:
        effective_tool, effective_action = _LEGACY_ALIASES[tool_name]

    async def handler(state: dict) -> dict:
        from app.tools.agent_tool import get_agent_tool
        from app.utils.templates import resolve_templates

        # Template authors reference `agent_name` on the sub-agent's runtime
        # meta slot. Fall back to "" (global lookup) if unavailable.
        agent_name = state.get("main_context", {}).get("agent_name", "") or ""
        tool = get_agent_tool(effective_tool, agent_name)
        if tool is None:
            logger.error(
                "[subagent_tool_missing] tool=%s agent_name=%s",
                effective_tool, agent_name,
            )
            variables = dict(state.get("variables") or {})
            variables[output_var] = {
                "status": "ERROR",
                "error_category": "system",
                "error": f"No tool registered with name {effective_tool!r}",
                "user_facing_message": "Internal configuration error.",
            }
            return {"variables": variables}

        if not effective_action:
            variables = dict(state.get("variables") or {})
            variables[output_var] = {
                "status": "ERROR",
                "error_category": "system",
                "error": f"tool_call_node {tool_name!r} missing action",
                "user_facing_message": "Internal configuration error.",
            }
            return {"variables": variables}

        resolved_params = resolve_templates(params_template, state)
        context = {
            "user_id": state.get("user_id", ""),
            "session_id": state.get("session_id", ""),
            "channel": state.get("channel", ""),
        }

        logger.info(
            "[subagent_tool_call.v1] tool=%s action=%s output_var=%s",
            effective_tool, effective_action, output_var,
        )

        try:
            result = await tool.dispatch(effective_action, resolved_params, context)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[subagent_tool_call_exception] tool=%s action=%s err=%s",
                effective_tool, effective_action, e,
            )
            result = {
                "status": "ERROR",
                "error_category": "transient",
                "error": str(e),
                "user_facing_message": "Something went wrong. Please try again.",
            }

        variables = dict(state.get("variables") or {})
        variables[output_var] = result

        # post_write runs only on success (no ERROR status).
        if not (isinstance(result, dict) and result.get("status") == "ERROR"):
            for k, v in post_write.items():
                variables[k] = v

        return {"variables": variables}

    return handler


register_node_type("tool_call_node", build_tool_call_node_factory)

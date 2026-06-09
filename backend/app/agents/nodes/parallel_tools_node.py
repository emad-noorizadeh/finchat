"""parallel_tools_node — declarative parallel fan-out to N AgentTool actions.

Use case: from a single graph point, call several read-only data tools at
once, wait for all of them, then route into one downstream synthesizer
(llm_node / parse_node / response_node) that has every result available in
`state.variables`.

Why not "fan out via multiple edges to N tool_call_nodes": the SubAgentState
reducer for `variables` is last-write-wins, so two concurrent tool_call_nodes
would clobber each other's writes. This node sidesteps that by performing
all dispatches inside ONE LangGraph node and returning a single merged
`variables` dict — so the existing reducer is happy.

Data schema:
  tools: [                         # required, non-empty
    {
      tool: str,                   # AgentTool name (required)
      action: str,                 # AgentTool action (required — no legacy alias)
      params: dict,                # templated via {{variables.X}}
      output_var: str,             # required — unique across siblings
      post_write: dict | null,     # optional flat resets applied on success
    },
    ...
  ]
  on_error: "collect" | "abort"    # default "collect"
  timeout_seconds: int | null      # optional per-call wall-time cap

Behaviour:
  on_error = "collect" (default): every entry runs; per-entry failures
    become ERROR-shaped dicts in variables[output_var]; siblings unaffected.
    The synthesizer sees partial data and decides what to do.
  on_error = "abort": the first failing entry sets its ERROR sentinel and
    cancels in-flight siblings; remaining-but-not-started entries don't run.
    Use this for compliance flows where partial results are unsafe.

post_write semantics match tool_call_node: only applied for entries whose
result is NOT an ERROR. Multiple entries' post_writes are merged into the
returned variables dict; later entries (by declaration order) win on key
conflicts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from app.agents.nodes import register_node_type
from app.agents.nodes.tool_call_node import dispatch_one, _is_error, _state_context

logger = logging.getLogger(__name__)


def build_parallel_tools_node_factory(data: dict) -> Callable:
    entries = data.get("tools") or []
    if not isinstance(entries, list) or not entries:
        raise ValueError("parallel_tools_node.data.tools must be a non-empty list")

    on_error = data.get("on_error") or "collect"
    if on_error not in ("collect", "abort"):
        raise ValueError(
            f"parallel_tools_node.data.on_error must be 'collect' or 'abort', got {on_error!r}"
        )

    timeout = data.get("timeout_seconds")
    if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
        raise ValueError("parallel_tools_node.data.timeout_seconds must be a positive number or null")

    # Validate per-entry shape once at compile time so authors get fast feedback.
    seen_output_vars: set[str] = set()
    compiled_entries: list[dict] = []
    for i, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ValueError(f"parallel_tools_node.data.tools[{i}] must be an object")
        tool_name = (raw.get("tool") or "").strip()
        action = (raw.get("action") or "").strip()
        output_var = (raw.get("output_var") or "").strip()
        params = raw.get("params") or {}
        post_write = raw.get("post_write") or {}

        if not tool_name:
            raise ValueError(f"parallel_tools_node.data.tools[{i}].tool is required")
        if not action:
            raise ValueError(
                f"parallel_tools_node.data.tools[{i}].action is required "
                f"(no legacy aliases — pass the canonical action explicitly)"
            )
        if not output_var:
            raise ValueError(f"parallel_tools_node.data.tools[{i}].output_var is required")
        if output_var in seen_output_vars:
            raise ValueError(
                f"parallel_tools_node.data.tools[{i}].output_var={output_var!r} "
                f"duplicates an earlier entry — output_vars must be unique within the node"
            )
        if not isinstance(params, dict):
            raise ValueError(f"parallel_tools_node.data.tools[{i}].params must be a dict")
        if not isinstance(post_write, dict):
            raise ValueError(f"parallel_tools_node.data.tools[{i}].post_write must be a flat dict")

        seen_output_vars.add(output_var)
        compiled_entries.append({
            "tool": tool_name,
            "action": action,
            "params": params,
            "output_var": output_var,
            "post_write": post_write,
        })

    async def handler(state: dict) -> dict:
        from app.utils.templates import resolve_templates

        agent_name = state.get("main_context", {}).get("agent_name", "") or ""
        context = _state_context(state)

        async def _one(entry: dict) -> tuple[dict, dict]:
            """Returns (entry, result). Never raises — exceptions inside
            dispatch_one are already caught and shaped as ERROR dicts."""
            resolved_params = resolve_templates(entry["params"], state)
            coro = dispatch_one(
                tool_name=entry["tool"],
                action=entry["action"],
                resolved_params=resolved_params,
                agent_name=agent_name,
                context=context,
            )
            if timeout is not None:
                try:
                    result = await asyncio.wait_for(coro, timeout=timeout)
                except asyncio.TimeoutError:
                    result = {
                        "status": "ERROR",
                        "error_category": "transient",
                        "error": f"timeout after {timeout}s",
                        "user_facing_message": "Something took too long. Please try again.",
                    }
            else:
                result = await coro
            return entry, result

        coros = [_one(e) for e in compiled_entries]
        logger.info(
            "[subagent_parallel_tools.v1] entries=%d on_error=%s timeout=%s",
            len(compiled_entries), on_error, timeout,
        )

        if on_error == "abort":
            # First failure cancels in-flight siblings. asyncio.gather with
            # return_exceptions=False raises on first error; we then need to
            # populate ERROR sentinels for entries that never started.
            try:
                pairs = await asyncio.gather(*coros)
            except Exception as e:  # noqa: BLE001
                # asyncio.gather already cancelled siblings; we lose per-entry
                # results. Mark every entry as aborted.
                logger.warning("[subagent_parallel_tools_abort] err=%s", e)
                variables = dict(state.get("variables") or {})
                for entry in compiled_entries:
                    variables[entry["output_var"]] = {
                        "status": "ERROR",
                        "error_category": "transient",
                        "error": f"aborted: {e}",
                        "user_facing_message": "Something went wrong. Please try again.",
                    }
                return {"variables": variables}
        else:
            # Collect mode: per-entry failures already became ERROR dicts via
            # dispatch_one, so gather sees no exceptions to propagate.
            pairs = await asyncio.gather(*coros)

        variables = dict(state.get("variables") or {})
        # Apply each entry's result + post_write in declaration order so later
        # entries' post_writes deterministically win on key conflicts.
        for entry, result in pairs:
            variables[entry["output_var"]] = result
            if not _is_error(result):
                for k, v in entry["post_write"].items():
                    variables[k] = v
        return {"variables": variables}

    return handler


register_node_type("parallel_tools_node", build_parallel_tools_node_factory)

"""llm_node — LLM call with a scoped system prompt + bound tool subset.

Not used by Transfer. For sub-agents that need fuzzy interpretation or
multi-turn LLM reasoning (Q&A lookups, intent classification). System
prompt + tool binding are both scoped per node — a commit-phase llm_node
binds only commit-phase tools.

Data schema:
  system_prompt: str
  tools: [str]                 # names from registry; subset bound here
  llm_variant: str             # default "sub_agent"
  output_schema: dict | null   # required for regulated templates (enforced in loader)
"""

from __future__ import annotations

import logging
from typing import Callable

from langchain_core.messages import SystemMessage

from app.agents.nodes import register_node_type

logger = logging.getLogger(__name__)


def build_llm_node_factory(data: dict) -> Callable:
    raw_prompt = data.get("system_prompt", "")
    tool_names = tuple(data.get("tools") or ())
    llm_variant = data.get("llm_variant", "sub_agent")
    # Auto-prepend the sub-agent context unless this node explicitly opts
    # out. The compiler injects `_agent_context` from template.context.
    include_context = data.get("include_context", True)
    agent_context = data.get("_agent_context", "") if include_context else ""
    system_prompt = (
        f"{agent_context}\n\n{raw_prompt}"
        if agent_context and raw_prompt
        else (agent_context or raw_prompt)
    )

    async def handler(state: dict) -> dict:
        from app.services.llm_service import get_llm
        from app.tools import get_tool
        from app.utils.templates import resolve_templates

        # Gather bound tools — schemas only (LangChain tool-binding convention).
        bound_schemas = []
        for name in tool_names:
            t = get_tool(name)
            if t:
                bound_schemas.append(await t.to_openai_schema())

        llm = get_llm(llm_variant)
        if bound_schemas:
            llm_bound = llm.bind_tools(bound_schemas)
        else:
            llm_bound = llm

        # Runtime substitution: {{variables.X}}, {{user_id}}, {{channel}}, etc.
        # All resolved against state at the moment this node runs, so authors
        # can weave upstream slot values + tool results into the system prompt.
        rendered_prompt = str(resolve_templates(system_prompt, state))
        messages = [SystemMessage(content=rendered_prompt)] + list(state.get("messages") or [])
        response = await llm_bound.ainvoke(messages)

        logger.info(
            "[subagent_llm.v1] tools_bound=%d has_tool_calls=%s",
            len(bound_schemas),
            bool(getattr(response, "tool_calls", None)),
        )

        return {"messages": [response]}

    return handler


register_node_type("llm_node", build_llm_node_factory)

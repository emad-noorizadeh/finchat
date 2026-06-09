"""Sub-agent node type registry. 8 node types.

Each handler is an async function `(state) -> dict` that returns a state
update (LangGraph convention). Nodes are registered by string key; the
template compiler maps `template.node.type` → handler factory.

All handlers written against the flat `SubAgentState`. No shared runtime
state, no hidden side channels — everything goes through state.
"""

from __future__ import annotations

from typing import Callable, Protocol


class NodeFactory(Protocol):
    """Factory takes the node's `data` dict + compile-time options,
    returns an async LangGraph node handler."""
    def __call__(self, data: dict) -> Callable: ...


_REGISTRY: dict[str, NodeFactory] = {}


def register_node_type(name: str, factory: NodeFactory) -> None:
    if name in _REGISTRY and _REGISTRY[name] is not factory:
        raise ValueError(f"node type {name!r} already registered")
    _REGISTRY[name] = factory


def get_node_factory(name: str) -> NodeFactory | None:
    return _REGISTRY.get(name)


def known_node_types() -> set[str]:
    return set(_REGISTRY.keys())


# Placeholder authors can drop into a node's system_prompt to position the
# sub-agent context manually. When present, the context is substituted at
# that spot instead of being auto-prepended. Resolution happens at factory
# build time because _agent_context lives in node.data, not in state.
_AGENT_CONTEXT_PLACEHOLDER = "{{agent_context}}"


def compose_system_prompt(raw_prompt: str, data: dict) -> str:
    """Compose a node's effective system prompt from authored content + the
    sub-agent's Context-tab content.

    Rules:
      1. If `{{agent_context}}` appears in raw_prompt, substitute the
         agent context at that position (any number of occurrences).
         When include_context is False, substitutes with empty string —
         the placeholder always drives placement, the toggle drives content.
      2. Otherwise, if include_context is True and both pieces are present,
         prepend context with a `\\n\\n` separator (legacy default).
      3. Else return whichever side is non-empty (or empty if both are).

    `{{variables.X}}`-style state substitutions inside the resulting prompt
    are resolved later at handler time by `resolve_templates`; nothing in
    this function reads state.
    """
    include_context = data.get("include_context", True)
    agent_context = data.get("_agent_context", "") if include_context else ""

    if _AGENT_CONTEXT_PLACEHOLDER in (raw_prompt or ""):
        return raw_prompt.replace(_AGENT_CONTEXT_PLACEHOLDER, agent_context)
    if agent_context and raw_prompt:
        return f"{agent_context}\n\n{raw_prompt}"
    return agent_context or raw_prompt


# --- Registrations (imports trigger register_node_type calls) ---

from app.agents.nodes import parse_node            # noqa: F401
from app.agents.nodes import tool_call_node        # noqa: F401
from app.agents.nodes import parallel_tools_node   # noqa: F401
from app.agents.nodes import condition_node        # noqa: F401
from app.agents.nodes import interrupt_node        # noqa: F401
from app.agents.nodes import llm_node              # noqa: F401
from app.agents.nodes import tool_node             # noqa: F401
from app.agents.nodes import response_node         # noqa: F401

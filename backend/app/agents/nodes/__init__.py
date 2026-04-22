"""Sub-agent node type registry. 7 node types.

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


# --- Registrations (imports trigger register_node_type calls) ---

from app.agents.nodes import parse_node          # noqa: F401
from app.agents.nodes import tool_call_node      # noqa: F401
from app.agents.nodes import condition_node      # noqa: F401
from app.agents.nodes import interrupt_node      # noqa: F401
from app.agents.nodes import llm_node            # noqa: F401
from app.agents.nodes import tool_node           # noqa: F401
from app.agents.nodes import response_node       # noqa: F401

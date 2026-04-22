"""condition_node — passthrough node that anchors conditional edges.

Evaluated at COMPILE time into a LangGraph conditional_edges group on the
source node. The handler itself is a no-op; routing happens via
`add_conditional_edges` with a router function that walks the edges in
array order, evaluates each predicate, returns the first match's target.

Runtime-injected priority-0 edge: the compiler prepends a synthetic edge
checking `has(variables.retry_exhausted_for_slot)`. If true, routes to the
runtime-default escalation node unless the template's interrupt_node
declared on_retry_exhausted — then it routes there instead.

Template data:
  (no fields required — edges carry the predicates)
"""

from __future__ import annotations

from typing import Callable

from app.agents.nodes import register_node_type


def build_condition_node_factory(data: dict) -> Callable:
    async def handler(state: dict) -> dict:
        # Passthrough. Routing happens in the conditional_edges router.
        return {}

    return handler


register_node_type("condition_node", build_condition_node_factory)

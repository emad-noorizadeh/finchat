"""AgentTool — a unified tool exposing multiple named actions.

Pattern:
  class FooOpsTool(AgentTool):
      name = "foo"
      agent_name = "foo_agent"    # "" = global / planner-callable
      description = "Foo operations"

      @action("do_thing", description="...", params_schema={...}, output_schema={...})
      async def do_thing(self, params: dict, context: dict) -> dict:
          ...

A sub-agent template's `tool_call_node` picks `{tool: "foo", action: "do_thing"}`.
The dispatcher calls `tool.dispatch("do_thing", params, context)` which invokes
the decorated method. This replaces the old per-agent closure pattern (ad-hoc
if/elif on a fake tool name).

Registration:
  register_agent_tool(FooOpsTool())
  - entered into AGENT_TOOL_REGISTRY keyed by (agent_name, tool.name)
  - agent_name="" means the tool is globally available to any sub-agent
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class ActionDecl:
    name: str
    description: str
    params_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    # Python attribute on the tool instance that implements this action.
    handler_attr: str = ""


def action(
    name: str,
    *,
    description: str = "",
    params_schema: dict | None = None,
    output_schema: dict | None = None,
) -> Callable:
    """Decorator — marks an AgentTool method as an action.

    The method receives (self, params: dict, context: dict) and returns a
    dict (or anything JSON-serialisable). `params_schema` / `output_schema`
    are used by the UI to auto-build forms and validate shapes.
    """
    def decorator(fn: Callable) -> Callable:
        setattr(fn, "__agent_action__", ActionDecl(
            name=name,
            description=description,
            params_schema=params_schema or {},
            output_schema=output_schema or {},
            handler_attr=fn.__name__,
        ))
        return fn
    return decorator


class AgentTool:
    """Base class for domain-service tools exposed to sub-agents.

    Subclasses set `name`, optionally `agent_name`, and decorate methods with
    @action(...). The class scans itself on __init__ to build an `actions`
    dict {name: ActionDecl}.
    """

    name: str = ""
    agent_name: str = ""   # "" means global (any sub-agent can use)
    description: str = ""
    scope: str = "sub_agent"  # "sub_agent" | "planner"

    def __init__(self):
        self.actions: dict[str, ActionDecl] = {}
        for attr_name, attr in inspect.getmembers(self):
            decl: ActionDecl | None = getattr(attr, "__agent_action__", None)
            if decl is None:
                continue
            self.actions[decl.name] = decl

    async def dispatch(self, action_name: str, params: dict, context: dict) -> Any:
        decl = self.actions.get(action_name)
        if decl is None:
            raise ValueError(f"{self.name!r} has no action {action_name!r}")
        handler: Callable[..., Any] | Callable[..., Awaitable[Any]] = getattr(self, decl.handler_attr)
        result = handler(params or {}, context or {})
        if inspect.isawaitable(result):
            result = await result
        return result

    def describe(self) -> dict:
        """JSON-shape for the /api/tools endpoint."""
        return {
            "name": self.name,
            "agent_name": self.agent_name,
            "description": self.description,
            "scope": self.scope,
            "actions": [
                {
                    "name": d.name,
                    "description": d.description,
                    "params_schema": d.params_schema,
                    "output_schema": d.output_schema,
                }
                for d in self.actions.values()
            ],
        }


# --- Registry ---

# Keyed by (agent_name, tool.name). agent_name="" means the tool is available
# to any sub-agent (no scoping). The dispatcher falls back to this bucket when
# the per-agent lookup misses.
AGENT_TOOL_REGISTRY: dict[tuple[str, str], AgentTool] = {}


def register_agent_tool(tool: AgentTool) -> None:
    key = (tool.agent_name, tool.name)
    if key in AGENT_TOOL_REGISTRY:
        logger.warning("[agent_tool_reregister] key=%s — overwriting", key)
    AGENT_TOOL_REGISTRY[key] = tool
    logger.info(
        "[agent_tool_registered] name=%s agent_name=%s actions=%d",
        tool.name, tool.agent_name or "<global>", len(tool.actions),
    )


def get_agent_tool(tool_name: str, agent_name: str = "") -> AgentTool | None:
    """Look up a tool by (agent_name, name). Falls back to the global bucket
    if no agent-scoped match exists."""
    t = AGENT_TOOL_REGISTRY.get((agent_name, tool_name))
    if t is not None:
        return t
    return AGENT_TOOL_REGISTRY.get(("", tool_name))


def list_agent_tools_for(agent_name: str) -> list[AgentTool]:
    """Every tool an agent can call: its own agent-scoped tools + globals."""
    out: list[AgentTool] = []
    for (scope_name, _), tool in AGENT_TOOL_REGISTRY.items():
        if scope_name == agent_name or scope_name == "":
            out.append(tool)
    # Stable order: globals last, agent-scoped first.
    out.sort(key=lambda t: (t.agent_name == "", t.name))
    return out


def list_all_agent_tools() -> list[AgentTool]:
    return list(AGENT_TOOL_REGISTRY.values())

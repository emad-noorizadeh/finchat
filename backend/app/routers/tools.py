from fastapi import APIRouter

from app.tools import get_all_tools, get_tool
from app.tools.agent_tool import list_all_agent_tools, list_agent_tools_for
from app.agents import is_agent_backed, _AGENT_TOOLS

router = APIRouter(prefix="/api/tools", tags=["tools"])


def _build_agent_tool_entry(name: str, lc_tool) -> dict:
    """Build a list entry for an agent-scoped LangChain @tool.

    Reads metadata from function attributes set by @tool_meta decorator.
    Unwraps StructuredTool.func to access the raw decorated function.
    """
    fn = getattr(lc_tool, "func", lc_tool)

    # Extract input schema from LangChain tool
    input_schema = {}
    if hasattr(lc_tool, "args_schema") and lc_tool.args_schema:
        schema = lc_tool.args_schema.model_json_schema()
        input_schema = {
            "type": "object",
            "properties": schema.get("properties", {}),
            "required": schema.get("required", []),
        }

    return {
        "name": name,
        "description": lc_tool.description if hasattr(lc_tool, "description") else "",
        "search_hint": "",
        "should_defer": False,
        "always_load": False,
        "is_read_only": getattr(fn, "tool_is_read_only", True),
        "is_concurrency_safe": getattr(fn, "tool_is_concurrency_safe", True),
        "has_workflow_instructions": False,
        "scope": "sub_agent",
        "widget": getattr(fn, "tool_widget", ""),
        "flow": list(getattr(fn, "tool_flow", ())),
        "validations": list(getattr(fn, "tool_validations", ())),
        "errors": list(getattr(fn, "tool_errors", ())),
        "agent_scoped": True,
        "agent": getattr(fn, "tool_agent", ""),
        "input_schema": input_schema,
    }


def _tool_scope(tool) -> str:
    """Which LLM sees this tool? Drives the Scope badge on the /tools UI.

    After v8: the Presenter is deterministic (no LLM), so every user-callable
    tool is Planner-scoped. Agent-backed tools (e.g., transfer_agent) are
    wrappers the Planner CAN call — they spawn a sub-agent, they're not
    sub-agent-internal. Sub-agent-scoped tools come from _AGENT_TOOLS.
    """
    return "planner"


@router.get("")
async def list_tools(agent_name: str = ""):
    """List registered tools.

    Query params:
      agent_name — scope the result to tools available to that sub-agent
                   (agent-scoped tools + globals). If empty, returns the full
                   global catalogue for the Planner.

    Each entry may include `actions: [{name, description, params_schema,
    output_schema}]` — declared by AgentTool subclasses. The builder UI uses
    this to render a per-action dropdown + auto-populated params form.
    """
    tools = get_all_tools()
    result = []
    for tool in [t for t in tools if not t.is_internal and not is_agent_backed(t.name)]:
        desc = await tool.description()
        result.append({
            "name": tool.name,
            "description": desc,
            "search_hint": tool.search_hint,
            "should_defer": tool.should_defer,
            "always_load": tool.always_load,
            "is_read_only": tool.is_read_only,
            "is_concurrency_safe": tool.is_concurrency_safe,
            "has_workflow_instructions": bool(tool.workflow_instructions),
            "scope": _tool_scope(tool),
            "widget": tool.widget,
            "flow": list(tool.flow),
            "validations": list(tool.validations),
            "errors": list(tool.errors),
            "agent_scoped": False,
            "agent": "",
        })

    # Include legacy agent-scoped LangChain tools (back-compat).
    for scope_name, agent_tools in _AGENT_TOOLS.items():
        for lc_tool in agent_tools:
            name = lc_tool.name if hasattr(lc_tool, "name") else ""
            if name:
                result.append(_build_agent_tool_entry(name, lc_tool))

    # AgentTool-based unified tools (the new pattern). Includes their
    # declared actions.
    candidates = list_agent_tools_for(agent_name) if agent_name else list_all_agent_tools()
    for t in candidates:
        result.append({
            "name": t.name,
            "description": t.description,
            "search_hint": "",
            "should_defer": False,
            "always_load": False,
            "is_read_only": False,
            "is_concurrency_safe": True,
            "has_workflow_instructions": False,
            "scope": t.scope,
            "widget": "",
            "flow": [],
            "validations": [],
            "errors": [],
            "agent_scoped": bool(t.agent_name),
            "agent": t.agent_name,
            "actions": [
                {
                    "name": d.name,
                    "description": d.description,
                    "params_schema": d.params_schema,
                    "output_schema": d.output_schema,
                }
                for d in t.actions.values()
            ],
        })

    return result


@router.get("/{tool_name}")
async def get_tool_detail(tool_name: str):
    """Get detailed info for a specific tool including schema."""
    # Check main registry first
    tool = get_tool(tool_name)
    if tool:
        desc = await tool.description()
        schema = await tool.input_schema()
        return {
            "name": tool.name,
            "description": desc,
            "search_hint": tool.search_hint,
            "should_defer": tool.should_defer,
            "always_load": tool.always_load,
            "is_read_only": tool.is_read_only,
            "is_concurrency_safe": tool.is_concurrency_safe,
            "has_workflow_instructions": bool(tool.workflow_instructions),
            "workflow_instructions": tool.workflow_instructions or None,
            "input_schema": schema,
            "widget": tool.widget,
            "flow": list(tool.flow),
            "validations": list(tool.validations),
            "errors": list(tool.errors),
            "agent_scoped": False,
            "agent": "",
        }

    # Check agent-scoped tools
    from app.agents import get_agent_scoped_tool
    lc_tool = get_agent_scoped_tool(tool_name)
    if lc_tool:
        return _build_agent_tool_entry(tool_name, lc_tool)

    return {"error": f"Tool '{tool_name}' not found"}

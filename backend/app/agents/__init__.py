"""Sub-agent registry — v4 template-backed.

Every sub-agent lives as one JSON template per channel under
app/agents/templates/. TransferAgentTool drives the compiled LangGraph
StateGraph via template_compiler + a thread-keyed tool_caller registry in
app/agents/runtime.py. State is isolated from the main orchestrator — the
sub-agent returns a ToolResult whose shape depends on the terminal
response_node's return_mode (widget / glass / to_presenter / to_orchestrator).

The (name, channel) registry below is populated at init_agents() time and
used by tool_search + channel-filter code to decide whether a sub-agent is
reachable for the current channel.
"""

from __future__ import annotations


# Kept minimal. Used by tool_search filtering via is_agent_backed / has_channel_variant.
_AGENT_NAMES: set[str] = set()
_AGENT_CHANNELS: dict[str, set[str]] = {}   # name → {channels it supports}
_AGENT_TOOLS: dict[str, list] = {}


def register_agent_channels(name: str, channels: tuple[str, ...]) -> None:
    """Called by the agent's owning module on import. Replaces the old
    register_agent(BaseSubAgent) flow; no class hierarchy required."""
    _AGENT_NAMES.add(name)
    _AGENT_CHANNELS.setdefault(name, set()).update(channels)


def is_agent_backed(name: str) -> bool:
    return name in _AGENT_NAMES


def has_channel_variant(name: str, channel: str) -> bool:
    return channel in _AGENT_CHANNELS.get(name, set())


def known_agent_names() -> set[str]:
    return set(_AGENT_NAMES)


# (agent_name, channel) → template.name lookup. Filled by init_agents().
# Two-level because a single sub-agent ships separate chat / voice templates
# (dispatcher shapes differ — chat is widget-first, voice is interrupt-heavy).
_AGENT_TEMPLATE: dict[str, dict[str, str]] = {}


def template_for_agent(agent_name: str, channel: str = "chat"):
    """Return the LoadedTemplate registered to (agent_name, channel), or None.

    If no channel-specific entry exists but the sub-agent has any template
    registered, prefer the 'chat' fallback — keeps older callers working.
    """
    from app.agents.templates import get_template
    by_channel = _AGENT_TEMPLATE.get(agent_name) or {}
    tpl_name = by_channel.get(channel) or by_channel.get("chat")
    return get_template(tpl_name) if tpl_name else None


# --- Agent-scoped tools (preserved surface) ---


def register_agent_scoped_tool(agent_name: str, tool):
    if agent_name not in _AGENT_TOOLS:
        _AGENT_TOOLS[agent_name] = []
    _AGENT_TOOLS[agent_name].append(tool)


def get_agent_scoped_tools(agent_name: str) -> list:
    return _AGENT_TOOLS.get(agent_name, [])


def get_agent_scoped_tool(name: str):
    for tools in _AGENT_TOOLS.values():
        for tool in tools:
            if hasattr(tool, "name") and tool.name == name:
                return tool
    return None


def init_agents():
    """Initialize sub-agents. Phase 1: register Transfer channel support.

    No DB seeding; templates are file-based. The registration happens here
    rather than at TransferAgentTool import time because the tool registry
    init order is already controlled by app.tools.init_tools.
    """
    from app.agents.templates import known_templates

    for template in known_templates():
        register_agent_channels(template.agent_name, tuple(template.supported_channels))
        channel_map = _AGENT_TEMPLATE.setdefault(template.agent_name, {})
        for ch in template.supported_channels:
            channel_map[ch] = template.name

    # Agent-scoped tools — kept accessible for any free-form llm_node in a
    # template that wants to bind transfer_money directly. Regulated
    # sub-agents like the Transfer template use tool_call_node + the
    # tool_caller closure in TransferAgentTool, which doesn't rely on this
    # registry, but non-regulated sub-agents may.
    from app.tools.transfer_actions import transfer_money
    register_agent_scoped_tool("transfer_money", transfer_money)

    # Auto-register every DB-backed non-regulated sub-agent as a Planner-
    # discoverable tool so user-authored agents become callable from the
    # main orchestrator without a code change.
    from app.tools.dynamic_sub_agent_tool import refresh_dynamic_sub_agent_tools
    refresh_dynamic_sub_agent_tools()

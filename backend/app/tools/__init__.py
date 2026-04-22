from app.tools.base import BaseTool

_REGISTRY: dict[str, BaseTool] = {}


def register_tool(tool: BaseTool):
    if not tool.should_defer and not tool.always_load:
        raise ValueError(f"Tool '{tool.name}' must set either should_defer or always_load")
    if tool.should_defer and tool.always_load:
        raise ValueError(f"Tool '{tool.name}' cannot set both should_defer and always_load")
    _REGISTRY[tool.name] = tool


def get_always_load_tools(channel: str = "chat") -> list[BaseTool]:
    """Always-loaded tools for the given channel."""
    from app.agents import is_agent_backed, has_channel_variant

    result = []
    for t in _REGISTRY.values():
        if not t.always_load:
            continue
        if channel not in t.channels:
            continue
        if is_agent_backed(t.name) and not has_channel_variant(t.name, channel):
            continue
        result.append(t)
    return result


def get_deferred_tools() -> list[BaseTool]:
    return [t for t in _REGISTRY.values() if t.should_defer]


def get_tool(name: str) -> BaseTool | None:
    return _REGISTRY.get(name)


def search_tools(query: str, exclude: list[str] | None = None, channel: str = "chat") -> list[BaseTool]:
    """Weighted keyword search over deferred tools.

    Scoring:
      - name match:        5x weight
      - search_hint match: 3x weight
      - combined overlap:  1x weight
    Returns top 5 by score, excluding tools in `exclude` list.
    Tools declaring the current channel only; agent-backed tools also
    filtered by channel variant availability.
    """
    from app.agents import is_agent_backed, has_channel_variant

    query_words = set(query.lower().split())
    exclude_set = set(exclude or [])
    scored = []

    for tool in get_deferred_tools():
        if tool.name in exclude_set:
            continue

        if channel not in tool.channels:
            continue

        # Cross-registry check: if this tool wraps a sub-agent,
        # verify the agent has a variant for the current channel
        if is_agent_backed(tool.name) and not has_channel_variant(tool.name, channel):
            continue

        name_words = set(tool.name.lower().replace("_", " ").split())
        hint_words = set(tool.search_hint.lower().split())

        name_matches = len(query_words & name_words)
        hint_matches = len(query_words & hint_words)
        all_searchable = name_words | hint_words
        overlap = len(query_words & all_searchable)

        score = (name_matches * 5.0) + (hint_matches * 3.0) + (overlap * 1.0)

        if score > 0:
            scored.append((score, tool))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [tool for _, tool in scored[:5]]


def get_all_tools() -> list[BaseTool]:
    return list(_REGISTRY.values())


def init_tools():
    """Import all tool modules to trigger registration."""
    from app.tools import tool_search  # noqa
    from app.tools import data_tools  # noqa — get_profile_data, get_accounts_data, get_transactions_data
    from app.tools import handoff  # noqa — present_widget
    from app.tools import knowledge_search  # noqa
    from app.tools import transfer_tool  # noqa — TransferAgentTool (planner entry)
    from app.tools import transfer_ops  # noqa — TransferOpsTool (sub-agent actions)
    from app.tools import refund_tool  # noqa — RefundAgentTool (planner entry)
    from app.tools import refund_ops  # noqa — RefundOpsTool (sub-agent actions)

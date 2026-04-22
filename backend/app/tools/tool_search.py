import json

from app.tools.base import BaseTool, ToolResult
from app.tools import register_tool, search_tools


class ToolSearchTool(BaseTool):
    name = "tool_search"
    always_load = True
    should_defer = False
    search_hint = "discover find available tools capabilities"
    is_read_only = True
    is_internal = True
    flow = (
        "Search registered tools by capability description",
        "Weighted scoring: name (5x), hint (3x), description (1x)",
        "Filter by channel and exclude already-loaded tools",
        "Return top matches with schemas for LLM binding",
    )
    validations = (
        "Circular discovery guard: max 2 searches per message",
        "Excludes internal and already-active tools",
    )
    errors = ("Returns empty list if no matching tools",)

    async def description(self, context=None):
        return (
            "Search for available tools by capability. Use this when you need a tool "
            "that isn't currently available. Describe what you need and matching tools "
            "will be returned with their descriptions."
        )

    async def input_schema(self):
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language description of the capability needed, e.g. 'transaction history' or 'account balance'",
                }
            },
            "required": ["query"],
        }

    def activity_description(self, input):
        return "Searching for tools..."

    async def execute(self, input: dict, context: dict) -> str:
        # Circular discovery guard
        search_calls = context.get("search_tool_calls", 0)
        if search_calls >= 2:
            return json.dumps({
                "message": "You've already searched for tools twice. Use the tools you've discovered."
            })

        query = input.get("query", "")
        # Exclude already-active tools, filter by channel
        active_tools = context.get("available_tools", [])
        channel = context.get("channel", "chat")
        matches = search_tools(query, exclude=active_tools, channel=channel)

        results = []
        for tool in matches:
            desc = await tool.description(context)
            results.append({
                "name": tool.name,
                "description": desc,
                "search_hint": tool.search_hint,
            })
        if not results:
            return json.dumps({"message": "No matching tools found", "query": query})
        return json.dumps(results)


register_tool(ToolSearchTool())

"""Handoff tools — signal graph routing, not data operations.

present_widget is a no-op tool whose presence in the Planner's AIMessage.tool_calls
is a flag for the router to hand off to the Presenter (or fast-path). Its execute
body is a safety net in case any code path actually invokes it — it just returns
an empty ToolResult.
"""

from app.tools.base import BaseTool, ToolResult
from app.tools import register_tool


class PresentWidgetTool(BaseTool):
    name = "present_widget"
    always_load = True
    should_defer = False
    channels = ("chat",)  # Voice never has widget emission; channel filter enforces
    is_read_only = True
    is_internal = True  # Operator tool — don't surface in UI lists

    async def description(self, context=None):
        return (
            "Hand off to the Presenter to render a widget from the data you've "
            "gathered this turn. Call this with NO arguments when you want to "
            "display structured information visually. The Presenter sees your "
            "accumulated data slots and picks the best widget. Do NOT write "
            "text content in the same message — the widget is self-describing. "
            "Do NOT call this in voice mode — always answer in prose. Do NOT "
            "call this for knowledge-search paraphrases — narrate them directly."
        )

    async def input_schema(self):
        return {"type": "object", "properties": {}}

    def activity_description(self, input):
        return "Handing off to presenter..."

    async def execute(self, input: dict, context: dict) -> ToolResult:
        # No-op safety net. The router intercepts the tool_call before this runs
        # in the normal flow; if it ever does reach here, return empty.
        return ToolResult(to_llm="")


register_tool(PresentWidgetTool())

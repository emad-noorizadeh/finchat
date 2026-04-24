from enum import Enum

from pydantic import BaseModel


class StreamEventType(str, Enum):
    TURN_STARTED = "turn_started"   # first event on every SSE stream — carries turn_id
    THINKING = "thinking"
    TOOL_START = "tool_start"
    TOOL_COMPLETE = "tool_complete"
    RESPONSE_CHUNK = "response_chunk"
    RESPONSE = "response"
    INTERRUPT = "interrupt"
    WIDGET = "widget"
    ERROR = "error"
    DONE = "done"


class StreamEvent(BaseModel):
    type: str
    content: str | None = None
    tool: str | None = None
    tool_args: dict | None = None
    result_preview: str | None = None
    data: dict | None = None
    error: str | None = None


# --- Factory functions ---

def turn_started_event(turn_id: str, session_id: str = "") -> StreamEvent:
    """Very first event on every SSE stream. Carries the turn_id so
    clients can capture it for bug reports, correlation with server logs,
    and UI-level tracking. session_id included for convenience when the
    client didn't retain it from the URL path."""
    return StreamEvent(
        type=StreamEventType.TURN_STARTED,
        data={"turn_id": turn_id, "session_id": session_id},
    )


def thinking_event(content: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.THINKING, content=content)


def tool_start_event(
    tool_name: str,
    tool_args: dict | None = None,
    label: str | None = None,
) -> StreamEvent:
    """A tool started running.

    `label` carries the user-friendly description (from
    BaseTool.activity_description) so the frontend can render it directly
    without its own name→label lookup. Falls back to "Running <name>..."
    if not provided. `tool_args` carries the actual args for debug surfacing.
    """
    return StreamEvent(
        type=StreamEventType.TOOL_START,
        tool=tool_name,
        tool_args=tool_args,
        content=label or f"Running {tool_name}...",
    )


def tool_complete_event(tool_name: str, result_preview: str = "") -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.TOOL_COMPLETE,
        tool=tool_name,
        result_preview=result_preview[:200] if result_preview else "",
    )


def response_chunk_event(content: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.RESPONSE_CHUNK, content=content)


def response_event(content: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.RESPONSE, content=content)


def widget_event(widget_data: dict) -> StreamEvent:
    return StreamEvent(type=StreamEventType.WIDGET, data=widget_data)


def interrupt_event(data: dict) -> StreamEvent:
    return StreamEvent(type=StreamEventType.INTERRUPT, data=data)


def error_event(error_message: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.ERROR, error=error_message)


def done_event() -> StreamEvent:
    return StreamEvent(type=StreamEventType.DONE)


def get_thinking_message(tool_name: str) -> str:
    """Friendly activity label for a tool.

    Delegates to the tool's `activity_description({})`. The stream uses the
    richer `tool_activity` custom-event pipeline (see app/agent/nodes.py)
    which passes real args to `activity_description`; this helper exists
    only as a fallback for callers that don't have args in hand.
    """
    from app.tools import get_tool
    tool = get_tool(tool_name)
    if tool:
        try:
            return tool.activity_description({})
        except Exception:
            pass
    return f"Running {tool_name}..."

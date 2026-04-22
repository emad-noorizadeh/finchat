"""P0.2 + Phase 1 router tests.

Covers:
- present_widget() mechanics (no-op ToolResult, router routes to Presenter
  regardless of tool return).
- post_tool_router content preservation on Presenter branch.
- Two-phase hop guard triggers at iteration_count >= 2 when no present_widget
  and not terminated; logs [hop_guard_triggered] with intended tools +
  content_len.
- Error path: a raising tool in tool_execute surfaces as an error ToolMessage
  (not re-raised up to the driver); turn loops back to the Planner.
"""

import asyncio
import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.nodes import (
    post_tool_router,
    hop_guard_fallback,
    reset_turn_metrics,
    current_turn_metrics,
    tool_execute,
    _TWO_PHASE_HOP_CAP,
)
from app.tools import register_tool, _REGISTRY
from app.tools.base import BaseTool, ToolResult
from app.tools.handoff import PresentWidgetTool


# --- P0.2: present_widget mechanics ---


def test_present_widget_execute_returns_empty_toolresult():
    """The tool is a safety-net no-op: empty ToolResult, no widget, no terminal."""
    tool = PresentWidgetTool()
    result = asyncio.run(tool.execute({}, {}))
    assert isinstance(result, ToolResult)
    assert result.to_llm == ""
    assert result.widget is None or result.widget == {} or not result.widget
    assert result.glass is None or result.glass == "" or not result.glass
    assert result.final is False


def test_post_tool_router_routes_to_presenter_when_present_widget_in_tool_calls():
    """Router intercepts present_widget regardless of the tool's own return value."""
    tc = {"name": "present_widget", "args": {}, "id": "call_1"}
    planner_msg = AIMessage(content="", tool_calls=[tc])
    tool_msg = ToolMessage(content="", tool_call_id="call_1", name="present_widget")
    state = {
        "messages": [HumanMessage(content="show my transactions"), planner_msg, tool_msg],
        "response_terminated": False,
        "go_to_presenter": False,
        "iteration_count": 1,
    }
    assert post_tool_router(state) == "presenter"


def test_post_tool_router_preserves_content_on_presenter_branch():
    """Compound AIMessage (content + present_widget) still routes to presenter.

    The router doesn't strip content; content stays in state.messages and
    streams via on_chat_model_stream events. This test just confirms the
    routing decision is stable regardless of content presence.
    """
    planner_msg = AIMessage(
        content="You were charged a $5 Savings Monthly Fee on 03/31...",
        tool_calls=[{"name": "present_widget", "args": {}, "id": "c1"}],
    )
    state = {
        "messages": [HumanMessage(content="why did I get a fee?"), planner_msg,
                     ToolMessage(content="", tool_call_id="c1", name="present_widget")],
        "response_terminated": False,
        "go_to_presenter": False,
        "iteration_count": 2,
    }
    assert post_tool_router(state) == "presenter"
    # And confirm the content survived on the AIMessage itself
    assert "Savings Monthly Fee" in state["messages"][-2].content


# --- Hop guard ---


def test_hop_guard_triggers_at_second_iteration_without_present_widget(caplog):
    """Iteration >= 2, no present_widget, no termination → hop_guard_fallback."""
    planner_msg = AIMessage(
        content="Let me also check the balance.",
        tool_calls=[{"name": "get_accounts_data", "args": {}, "id": "c2"}],
    )
    state = {
        "messages": [HumanMessage(content="why is X?"), planner_msg,
                     ToolMessage(content="{}", tool_call_id="c2", name="get_accounts_data")],
        "response_terminated": False,
        "go_to_presenter": False,
        "iteration_count": 2,
    }
    reset_turn_metrics()
    with caplog.at_level(logging.INFO, logger="app.agent.nodes"):
        decision = post_tool_router(state)
    assert decision == "hop_guard_fallback"

    m = current_turn_metrics()
    assert m is not None
    assert m["hop_guard_triggered"] is True
    assert m["hop_guard_intended_tools"] == ["get_accounts_data"]
    assert m["hop_guard_content_len"] > 0  # narrate-and-loop signal

    # Log line present with the required keys
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "[hop_guard_triggered]" in text
    assert "intended_tools=['get_accounts_data']" in text
    assert "content_len=" in text


def test_hop_guard_does_not_trigger_when_present_widget_handoff_exists():
    """Present_widget wins over hop guard — even at iteration >= 2."""
    planner_msg = AIMessage(
        content="Here's the fee explanation…",
        tool_calls=[
            {"name": "get_transactions_data", "args": {}, "id": "c1"},
            {"name": "present_widget", "args": {}, "id": "c2"},
        ],
    )
    state = {
        "messages": [HumanMessage(content="why did I get a fee?"), planner_msg,
                     ToolMessage(content="{}", tool_call_id="c1", name="get_transactions_data"),
                     ToolMessage(content="", tool_call_id="c2", name="present_widget")],
        "response_terminated": False,
        "go_to_presenter": False,
        "iteration_count": 2,
    }
    reset_turn_metrics()
    assert post_tool_router(state) == "presenter"
    m = current_turn_metrics()
    assert m is not None
    assert m["hop_guard_triggered"] is False


def test_hop_guard_does_not_trigger_when_terminated():
    """Sub-agent widget already terminated; post_tool_router returns end."""
    state = {
        "messages": [HumanMessage(content="…"),
                     AIMessage(content="", tool_calls=[{"name": "transfer_agent", "args": {}, "id": "c"}]),
                     ToolMessage(content="", tool_call_id="c", name="transfer_agent")],
        "response_terminated": True,
        "go_to_presenter": False,
        "iteration_count": 2,
    }
    reset_turn_metrics()
    assert post_tool_router(state) == "end"


def test_hop_guard_first_iteration_still_loops():
    """Iteration 1 is the normal gather phase — loop back to Planner normally."""
    planner_msg = AIMessage(
        content="",
        tool_calls=[{"name": "get_transactions_data", "args": {}, "id": "c1"}],
    )
    state = {
        "messages": [HumanMessage(content="why did I get a fee?"), planner_msg,
                     ToolMessage(content="{}", tool_call_id="c1", name="get_transactions_data")],
        "response_terminated": False,
        "go_to_presenter": False,
        "iteration_count": 1,
    }
    reset_turn_metrics()
    assert post_tool_router(state) == "planner_llm"
    m = current_turn_metrics()
    assert m is not None
    assert m["hop_guard_triggered"] is False


def test_hop_guard_fallback_sets_response_terminated_and_emits_widget(monkeypatch):
    """The fallback node emits a text_card widget and terminates.

    dispatch_custom_event needs a LangGraph run context to write to — we
    patch it with a collector so the unit test can assert on the event
    payload without spinning up a full graph.
    """
    emitted: list[tuple[str, dict]] = []

    def _fake_dispatch(name, data, **_kwargs):
        emitted.append((name, data))

    monkeypatch.setattr("app.agent.nodes.dispatch_custom_event", _fake_dispatch)

    state = {
        "session_id": "test-session",
        "channel": "chat",
        "messages": [],
    }
    out = asyncio.run(hop_guard_fallback(state))
    assert out["response_terminated"] is True
    assert out["hop_guard_triggered"] is True
    # Exactly one widget event dispatched.
    assert len(emitted) == 1
    name, widget = emitted[0]
    assert name == "widget"
    assert widget.get("widget") == "text_card"
    # Title signals the fallback reason.
    assert "stuck" in (widget.get("title") or "").lower()


# --- Error path: raising tool → error ToolMessage, loop-back ---


class _RaisingTool(BaseTool):
    name = "_test_raising_tool"
    always_load = False
    should_defer = True
    channels = ("chat", "voice")
    is_read_only = True

    async def description(self, context=None):
        return "Test fixture — raises on execute."

    async def input_schema(self):
        return {"type": "object", "properties": {}}

    async def execute(self, input, context):
        raise ValueError("simulated tool failure")


@pytest.fixture
def _raising_tool_registered():
    tool = _RaisingTool()
    register_tool(tool)
    yield tool
    _REGISTRY.pop(tool.name, None)


def test_raising_tool_surfaces_as_error_toolmessage_not_exception(_raising_tool_registered):
    """tool_execute catches the exception, writes an error ToolMessage,
    does NOT re-raise. Graph can loop back to the Planner with the error
    content in the Planner's context for turn-2 narration."""
    planner_msg = AIMessage(
        content="",
        tool_calls=[{"name": "_test_raising_tool", "args": {}, "id": "c1"}],
    )
    state = {
        "messages": [HumanMessage(content="trigger the raise"), planner_msg],
        "user_id": "u",
        "session_id": "s",
        "channel": "chat",
        "available_tools": ["_test_raising_tool"],
        "tool_schemas": [],
        "search_tool_calls": 0,
        "knowledge_sources": [],
        "variables": {},
        "variables_order": {},
        "variables_counter": 0,
    }
    reset_turn_metrics()
    out = asyncio.run(tool_execute(state))

    # The error landed in a ToolMessage, not a Python exception.
    tool_msgs = out.get("messages") or []
    assert len(tool_msgs) == 1
    tm = tool_msgs[0]
    assert isinstance(tm, ToolMessage)
    assert "simulated tool failure" in tm.content or "error" in tm.content.lower()

    # Not terminated — graph can loop back.
    assert out.get("response_terminated") is False

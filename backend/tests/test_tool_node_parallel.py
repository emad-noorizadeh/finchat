"""Phase 0: tool_node fans out tool_calls with asyncio.gather.

Asserts wall-time ≈ max(call_durations), not sum, when the LLM emits
multiple tool_calls in one AIMessage.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.nodes.tool_node import build_tool_node_factory


SLOW = 0.20  # per-call sleep
SLACK = 0.10  # max scheduler overhead before we'd call it serial


def _ai_with_calls(calls):
    msg = AIMessage(content="")
    msg.tool_calls = calls
    return msg


def _state_with_caller(caller, tool_calls):
    return {
        "messages": [_ai_with_calls(tool_calls)],
        "_tool_caller": caller,
    }


@pytest.mark.asyncio
async def test_tool_node_runs_tool_calls_in_parallel():
    async def slow_tool(*, tool_name, action, params, state):
        await asyncio.sleep(SLOW)
        return {"tool": tool_name, "ok": True}

    handler = build_tool_node_factory({})
    tool_calls = [
        {"id": "c1", "name": "t1", "args": {}},
        {"id": "c2", "name": "t2", "args": {}},
        {"id": "c3", "name": "t3", "args": {}},
    ]

    t0 = time.perf_counter()
    out = await handler(_state_with_caller(slow_tool, tool_calls))
    elapsed = time.perf_counter() - t0

    assert elapsed < SLOW + SLACK, (
        f"tool_node ran calls serially: elapsed={elapsed:.3f}s "
        f"(threshold={SLOW + SLACK:.3f}s for parallel; "
        f"sequential would be ~{SLOW * len(tool_calls):.3f}s)"
    )
    assert len(out["messages"]) == 3
    assert all(isinstance(m, ToolMessage) for m in out["messages"])


@pytest.mark.asyncio
async def test_tool_node_preserves_call_id_order():
    """asyncio.gather preserves input order — tool_call_id pairings stay
    stable so the LLM's next turn can match outputs to its emitted calls."""
    async def echo_caller(*, tool_name, action, params, state):
        return {"echoed": tool_name}

    handler = build_tool_node_factory({})
    tool_calls = [
        {"id": "alpha", "name": "t_alpha", "args": {}},
        {"id": "beta", "name": "t_beta", "args": {}},
        {"id": "gamma", "name": "t_gamma", "args": {}},
    ]
    out = await handler(_state_with_caller(echo_caller, tool_calls))
    assert [m.tool_call_id for m in out["messages"]] == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_tool_node_single_failure_does_not_abort_siblings():
    """One tool's exception turns into an error-shaped ToolMessage; the
    other tools still run and return their results."""
    async def flaky_caller(*, tool_name, action, params, state):
        if tool_name == "boom":
            raise RuntimeError("kaboom")
        return {"ok": tool_name}

    handler = build_tool_node_factory({})
    tool_calls = [
        {"id": "c1", "name": "boom", "args": {}},
        {"id": "c2", "name": "fine", "args": {}},
    ]
    out = await handler(_state_with_caller(flaky_caller, tool_calls))
    assert len(out["messages"]) == 2
    assert "kaboom" in out["messages"][0].content
    assert "fine" in out["messages"][1].content

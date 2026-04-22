"""Integration test: content-before-tool-calls streaming order.

The compound-response prompt in enrichment.py instructs the Planner to emit
narration content BEFORE calling `present_widget()` in two-phase turn 2.
That relies on OpenAI's streaming delta order matching the serialization
order of the AIMessage. Most gpt-5 responses serialize content first, but
the API does not guarantee it — so this test captures the raw stream and
asserts the first non-empty delta is content, not a tool_call.

If this test fails intermittently, remediation (in escalation order):
  (a) Add a prompt instruction: "Emit your narration before calling
      present_widget()." — already present in enrichment.py.
  (b) Switch turn-2 to OpenAI's structured-output mode with an
      ordered-field schema.
  (c) Buffer the compound AIMessage in the router and re-dispatch content
      before widget (last resort).

This test is skipped automatically when no OPENAI_API_KEY is set, so it
won't block CI runs lacking credentials. Run it manually with credentials
before shipping prompt changes.

Run: pytest tests/test_streaming_order.py -v -s
"""

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Requires OPENAI_API_KEY for live API streaming test.",
)

# Number of independent trials. Single-trial would be brittle; 10 trials
# gives us a realistic read on the model's streaming order behavior.
_TRIALS = int(os.environ.get("STREAMING_ORDER_TRIALS", "10"))


def _prompt_for_turn_2() -> list:
    """Simulate the Planner's turn-2 context: user asked a "why" question,
    turn 1 already gathered data + KB context. Turn 2 should narrate AND
    optionally emit present_widget().

    We construct a minimal synthetic context rather than running the full
    graph so this test isolates the streaming-order property.
    """
    from langchain_core.messages import (
        SystemMessage, HumanMessage, AIMessage, ToolMessage,
    )
    return [
        SystemMessage(content=(
            "You are a financial assistant. The user asked why they got a "
            "fee. You have already called get_transactions_data and "
            "knowledge_search in turn 1. This is turn 2. "
            "Narrate the explanation as prose, THEN (only if a widget adds "
            "value) call present_widget(). Emit your narration content "
            "BEFORE any tool_calls in your message."
        )),
        HumanMessage(content="why did I get a fee on my savings?"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_transactions_data", "args": {"view": "search", "query": "fee"}, "id": "t1"},
                {"name": "knowledge_search", "args": {"query": "savings monthly fee policy"}, "id": "t2"},
            ],
        ),
        ToolMessage(
            content='{"shape":"flat","transactions":[{"description":"Savings Monthly Fee","amount":"$5.00","date":"03/31/2026"}],"total":1}',
            tool_call_id="t1",
            name="get_transactions_data",
        ),
        ToolMessage(
            content='{"passages":[{"title":"Savings Fee Policy","text":"A $5 monthly maintenance fee applies when the average daily balance falls below $500."}]}',
            tool_call_id="t2",
            name="knowledge_search",
        ),
    ]


def _first_non_empty_delta_kind(stream_chunks: list) -> str:
    """Scan the accumulated streaming deltas and return the kind of the
    FIRST chunk that carried any user-visible signal.

    Returns "content" | "tool_call" | "empty".
    """
    for chunk in stream_chunks:
        # content delta
        content = getattr(chunk, "content", None)
        if isinstance(content, str) and content.strip():
            return "content"
        # tool_call delta
        tc_chunks = getattr(chunk, "tool_call_chunks", None) or []
        for tcc in tc_chunks:
            # tool_call_chunks may have an incremental name or args
            if (tcc.get("name") or tcc.get("args")):
                return "tool_call"
    return "empty"


@pytest.mark.asyncio
async def test_planner_streams_content_before_tool_calls():
    """Run {_TRIALS} independent invocations; assert content-first in all."""
    from app.services.llm_service import get_llm, reset as reset_llm
    from app.tools import get_always_load_tools

    # Bind present_widget so the Planner has the option to call it.
    reset_llm()
    llm = get_llm()
    tools = get_always_load_tools("chat")
    schemas = [await t.to_openai_schema() for t in tools]
    llm = llm.bind_tools(schemas)

    bad_trials = 0
    first_deltas: list[str] = []
    for _ in range(_TRIALS):
        messages = _prompt_for_turn_2()
        chunks = []
        async for chunk in llm.astream(messages):
            chunks.append(chunk)
        kind = _first_non_empty_delta_kind(chunks)
        first_deltas.append(kind)
        if kind != "content":
            bad_trials += 1

    print(f"\n[streaming_order] trials={_TRIALS} first_deltas={first_deltas}")
    # All trials must emit content first. Intermittent tool-call-first
    # means the prompt instruction isn't enforcing ordering — escalate
    # remediation per the module docstring.
    assert bad_trials == 0, (
        f"{bad_trials}/{_TRIALS} trials emitted tool_calls before content. "
        f"Escalate remediation (structured output or buffer-and-reorder)."
    )

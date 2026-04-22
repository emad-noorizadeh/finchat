"""Sub-agent state schema.

State is ISOLATED from the main orchestrator. Separate checkpointer thread,
separate messages, separate variables. Communication with the main
orchestrator happens only through invocation input + ToolResult return
(see app/tools/transfer_tool.py).
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class SubAgentState(TypedDict, total=False):
    # Conversation history — sub-agent's own. Does NOT merge with main orchestrator.
    messages: Annotated[list, add_messages]

    # Session context propagated from the main orchestrator.
    user_id: str
    session_id: str
    channel: str                                # "chat" | "voice"

    # Read-only view of main-orchestrator context (Phase 1: empty;
    # Phase 2+: user preferences, recent-message references, etc.).
    # Sub-agent reads via predicates. Never writes back.
    main_context: dict

    # Slot scratchpad — tool-call outputs, parsed values, derived state.
    # Written by parse_node / tool_call_node / post_write / runtime hooks.
    variables: dict

    # Terminal marker set by response_node on exit. Sub-agent tool checks
    # this on next invocation: terminal → fresh state; else → resume.
    _terminal: bool

    # --- Runtime safety (retry tracking, v4 §2) ---
    # Count of consecutive parses where last_prompted_slot didn't change.
    # Reset when the slot is finally filled or corrected.
    parse_retry_count: dict

    # Which slot the most recent interrupt_node targeted. None when the
    # interrupt wasn't slot-scoped (e.g., general "what do you want?").
    last_prompted_slot: str | None

    # Set by the runtime when parse_retry_count[slot] >= 3. Dispatcher's
    # runtime-injected priority-0 edge picks up on this and routes to
    # escalation (template-specified on_retry_exhausted, or runtime default).
    retry_exhausted_for_slot: str | None

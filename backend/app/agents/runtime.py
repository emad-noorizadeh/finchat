"""Per-thread runtime registry for sub-agent tool callers.

tool_call_node reads `_tool_caller` from state today, but closures cannot
round-trip through the checkpointer (JsonPlusSerializer chokes on closures).
Instead, sub-agent drivers register a tool_caller keyed by `thread_id`
before invoking the graph and clear it when the graph terminates. The node
reads it back via `thread_id` from its runtime context.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

# Module-level thread-id → tool_caller map. Lives for the process lifetime;
# entries are cleared by the driver on terminal return.
_TOOL_CALLERS: dict[str, Callable] = {}

# Accumulated inner-graph state per sub-agent thread_id. Populated across
# outer interrupts — the inner graph runs without its own checkpointer; the
# driver snapshots the inner variables + messages here so the next outer
# re-entry resumes from the same state. Cleared on terminal return or on
# process restart.
_INNER_STATE: dict[str, dict] = {}


def save_inner_state(thread_id: str, state: dict) -> None:
    _INNER_STATE[thread_id] = state


def load_inner_state(thread_id: str) -> dict | None:
    return _INNER_STATE.get(thread_id)


def clear_inner_state(thread_id: str) -> None:
    _INNER_STATE.pop(thread_id, None)

# Active thread_id for the currently-executing node. Set/cleared by the
# driver around graph astream() calls; node handlers read via current_thread.
_ACTIVE_THREAD: ContextVar[str | None] = ContextVar("_subagent_active_thread", default=None)


def register_tool_caller(thread_id: str, tool_caller: Callable) -> None:
    _TOOL_CALLERS[thread_id] = tool_caller


def unregister_tool_caller(thread_id: str) -> None:
    _TOOL_CALLERS.pop(thread_id, None)


def current_thread() -> str | None:
    return _ACTIVE_THREAD.get()


def set_active_thread(thread_id: str | None):
    """Context manager-style helper. Returns the previous token so callers can
    restore it after the graph run completes."""
    return _ACTIVE_THREAD.set(thread_id)


def reset_active_thread(token) -> None:
    _ACTIVE_THREAD.reset(token)


def get_tool_caller_for(thread_id: str) -> Callable | None:
    return _TOOL_CALLERS.get(thread_id)

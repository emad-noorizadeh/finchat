from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    user_id: str
    session_id: str
    available_tools: list[str]
    tool_schemas: list[dict]
    iteration_count: int
    enrichment_context: str  # Final system prompt (base + knowledge context injected)
    base_system_prompt: str  # Base prompt with {{knowledge_context}} placeholder
    knowledge_sources: list[dict]  # [{title, url}] populated by tools returning ToolResult(sources=...). Reset to [] every turn by enrich(). Appended to final response as a Sources block (chat only).
    search_tool_calls: int  # Circular discovery guard — tracks tool_search invocations per message
    channel: str  # "chat" | "voice" — determines which agent variants are available
    response_terminated: bool  # True when a tool emits a final response (widget, glass, or final=True) — skip LLM, go to END
    last_executed_tools: list[str]  # Names of tools run in the most recent tool_execute step. Reset by enrich() each turn. llm_call uses this to inject response_instructions on the iteration right after a tool ran.
    variables: dict  # Data-tool output slots keyed by tool.output_var. Reset per turn by enrich(). Read by render tools via context["variables"].
    variables_order: dict  # slot_name → monotonic order number within the current turn. Populated by tool_execute when a slot is written. Used by the Presenter for rule 3 section ordering.
    variables_counter: int  # Monotonic per-turn counter. Incremented in tool_execute each time a slot is written.
    go_to_presenter: bool  # Set by tool_execute when a sub-agent returns ToolResult(go_to_presenter=True). post_tool_router routes to Presenter.
    hop_guard_triggered: bool  # Set by hop_guard_fallback node when the two-phase cap force-terminates the turn. Flagged into [turn_summary.v1] so observability can distinguish "Planner stalled looping" from normal text_card fallbacks.

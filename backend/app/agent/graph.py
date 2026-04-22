from langgraph.graph import StateGraph, END

from app.agent.state import AgentState
from app.agent.nodes import (
    enrich,
    llm_call,
    tool_execute,
    should_route,
    post_tool_router,
    hop_guard_fallback,
)
from app.agent.presenter import presenter


def build_agent_graph(checkpointer=None):
    """Build and compile the Planner + deterministic-Presenter graph.

    Flow:
      enrich → planner_llm → should_route
         ├─ text_fast_path                            → END
         ├─ tool_execute → post_tool_router
         │    ├─ end                 (response_terminated — sub-agent widget, glass, final)
         │    ├─ presenter           (present_widget handoff — rules engine picks + emits + ends)
         │    ├─ hop_guard_fallback  (two-phase cap hit — emit text_card widget, end)
         │    └─ planner_llm         (ReAct loop — data tools only)
         └─ end (max iterations)

    Key invariant: when the Planner emitted tool_calls, tool_execute runs them
    BEFORE any branching. OpenAI rejects any shape where an assistant message
    with tool_calls is followed by an LLM call without corresponding ToolMessages.

    The Presenter and hop_guard_fallback are both terminal — they emit the
    widget via dispatch_custom_event and set response_terminated=True.
    """
    graph = StateGraph(AgentState)

    graph.add_node("enrich", enrich)
    graph.add_node("planner_llm", llm_call)
    graph.add_node("tool_execute", tool_execute)
    graph.add_node("presenter", presenter)
    graph.add_node("hop_guard_fallback", hop_guard_fallback)

    graph.set_entry_point("enrich")
    graph.add_edge("enrich", "planner_llm")

    # Planner → routing (text_fast_path OR run tools first)
    graph.add_conditional_edges(
        "planner_llm",
        should_route,
        {
            "text_fast_path": END,
            "tool_execute": "tool_execute",
            "end": END,
        },
    )

    # tool_execute → end | presenter | hop_guard_fallback | planner_llm (ReAct loop)
    graph.add_conditional_edges(
        "tool_execute",
        post_tool_router,
        {
            "end": END,
            "presenter": "presenter",
            "hop_guard_fallback": "hop_guard_fallback",
            "planner_llm": "planner_llm",
        },
    )

    # Presenter is terminal — it emits the widget and ends.
    graph.add_edge("presenter", END)
    graph.add_edge("hop_guard_fallback", END)

    return graph.compile(checkpointer=checkpointer)

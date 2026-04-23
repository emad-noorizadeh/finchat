import asyncio
import json
import logging
import time
from contextvars import ContextVar

from langchain_core.messages import AIMessage, ToolMessage, SystemMessage

from langchain_core.callbacks import dispatch_custom_event

from app.config import settings
from app.tools import get_tool, get_always_load_tools
from app.tools.base import ToolResult
from app.agent.state import AgentState

logger = logging.getLogger(__name__)
_LARGE_TOOL_RESULT_CHARS = 2000


# --- Turn-level telemetry ---
#
# One accumulator per turn, shared across all nodes via a ContextVar. The
# chat router reset_turn_metrics() at turn start and emit_turn_summary() at
# turn end; each node calls into `current_turn_metrics()` to bump counters
# without passing state through the graph. Safe under asyncio — ContextVar
# scopes per task.
_TURN_METRICS: ContextVar[dict | None] = ContextVar("_finchat_turn_metrics", default=None)


def reset_turn_metrics() -> dict:
    """Start a fresh turn telemetry accumulator. Returns the dict so the
    caller can stash it if they want to inspect it directly."""
    acc = {
        "turn_started_at": time.perf_counter(),
        "llm_calls": 0,
        "llm_total_ms": 0.0,
        "tool_calls": 0,
        "tool_total_ms": 0.0,
        "tool_names": [],
        # Per-phase tool lists (phase = one Planner LLM call). phase 1 = first
        # Planner turn's tools; phase 2 = second Planner turn's tools; etc.
        # Used to derive turn2_tool_delta (set difference, phase2 \ phase1) and
        # turn2_tool_repeat (intersection non-empty). Both fields are absent
        # from turn_summary on single-turn shapes.
        "tool_names_by_phase": [],
        "iterations": 0,
        "enrich_ms": 0.0,
        "presenter_ms": 0.0,
        "rehydrated": False,
        # Response-shape tracking. Any widget emission (sub-agent, Presenter,
        # or fast-path) flips widget_emitted=True. Any streamed content chunk
        # flips prose_emitted=True. Combined at turn_summary time into
        # response_shape: prose_plus_widget | widget_only | no_widget.
        "widget_emitted": False,
        "prose_emitted": False,
        "hop_guard_triggered": False,
        "hop_guard_intended_tools": [],
        "hop_guard_content_len": 0,
    }
    _TURN_METRICS.set(acc)
    return acc


def current_turn_metrics() -> dict | None:
    return _TURN_METRICS.get()


def _compute_response_shape(m: dict) -> str:
    """Derive the three-valued response_shape from turn-level signals."""
    if m.get("widget_emitted"):
        return "prose_plus_widget" if m.get("prose_emitted") else "widget_only"
    return "no_widget"


def emit_turn_summary(*, exit_reason: str, session_id: str = "", user_id: str = "", turn_id: str = "") -> None:
    """Log a single aggregated line summarising the whole turn. Called by
    the chat router once the graph stream finishes."""
    m = _TURN_METRICS.get()
    if not m:
        return
    total_ms = (time.perf_counter() - m["turn_started_at"]) * 1000

    response_shape = _compute_response_shape(m)
    phases = m.get("tool_names_by_phase") or []

    # turn2_* fields are ABSENT on single-turn shapes (one phase or fewer),
    # not "empty/false." Dashboards filter on presence, not value. Absence
    # itself is the positive signal that the turn didn't have a phase 2.
    turn2_parts = ""
    if len(phases) >= 2:
        phase1 = set(phases[0])
        phase2 = set(phases[1])
        delta = sorted(phase2 - phase1)
        repeat = bool(phase2 & phase1)
        turn2_parts = f" turn2_tool_delta={','.join(delta) or '-'} turn2_tool_repeat={repeat}"

    from app.config import settings
    prompt_rev = getattr(settings, "planner_prompt_revision", "unknown")

    logger.info(
        "[turn_summary.v1] session=%s user=%s turn=%s exit=%s total_ms=%.0f "
        "iterations=%d llm_calls=%d llm_ms=%.0f tool_calls=%d tool_ms=%.0f "
        "enrich_ms=%.0f presenter_ms=%.0f rehydrated=%s "
        "response_shape=%s planner_prompt_revision=%s hop_guard_triggered=%s%s "
        "tools=%s",
        session_id or "?", user_id or "?", turn_id or "?",
        exit_reason, total_ms,
        m["iterations"], m["llm_calls"], m["llm_total_ms"],
        m["tool_calls"], m["tool_total_ms"],
        m["enrich_ms"], m["presenter_ms"], m["rehydrated"],
        response_shape, prompt_rev, m.get("hop_guard_triggered", False),
        turn2_parts,
        m["tool_names"],
    )
    _TURN_METRICS.set(None)

# --- Context compaction (Phase 1) ---
#
# Hard-coded defaults chosen to keep latency stable without losing context
# for normal flows. Tune via settings later if metrics show we need to.
#   * We keep the last 5 user turns intact end-to-end.
#   * Older ToolMessages with payloads > 600 chars are collapsed to a
#     placeholder — their tool_call_id stays wired to the preceding
#     AIMessage so LangChain's tool-calling validator is happy.
_TRIM_KEEP_RECENT_TURNS = 5
_TRIM_TOOL_PAYLOAD_CHARS = 600


def _trim_messages_for_llm(messages: list) -> list:
    """Compact history before sending to the LLM.

    Strategy: keep every message object (so AIMessage.tool_calls stay paired
    with their ToolMessages), but replace the CONTENT of old / large
    ToolMessages with a short placeholder. This caps per-turn token growth
    without breaking the conversation structure.

    Nothing is mutated — we return a new list; original `state["messages"]`
    is unchanged.
    """
    from langchain_core.messages import HumanMessage

    if not messages:
        return messages

    human_positions = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if len(human_positions) <= _TRIM_KEEP_RECENT_TURNS:
        return messages  # short sessions — no trimming needed

    # Cutoff = index of the Nth-from-last HumanMessage. Everything ≥ cutoff
    # is "recent" and kept intact; earlier ToolMessages are candidates for
    # collapse.
    cutoff = human_positions[-_TRIM_KEEP_RECENT_TURNS]

    trimmed_count = 0
    trimmed_chars = 0
    out: list = []
    for i, m in enumerate(messages):
        if i >= cutoff or not isinstance(m, ToolMessage):
            out.append(m)
            continue
        content = m.content if isinstance(m.content, str) else str(m.content)
        if len(content) <= _TRIM_TOOL_PAYLOAD_CHARS:
            out.append(m)
            continue

        tool_name = getattr(m, "name", None) or ""
        label = f"for {tool_name} " if tool_name else ""
        placeholder = f"[older tool result {label}omitted — {len(content)} chars]"
        trimmed_count += 1
        trimmed_chars += len(content) - len(placeholder)
        out.append(ToolMessage(
            content=placeholder,
            tool_call_id=m.tool_call_id,
            name=tool_name or None,
        ))

    if trimmed_count:
        logger.info(
            "[context_trim] trimmed_tool_results=%d chars_saved=%d total_messages=%d kept_turns=%d",
            trimmed_count, trimmed_chars, len(messages), _TRIM_KEEP_RECENT_TURNS,
        )
    return out


async def enrich(state: AgentState) -> dict:
    """Per-turn setup: assemble system prompt, refresh always-loaded tool schemas."""
    _enrich_start = time.perf_counter()
    channel = state.get("channel", "chat")
    user_id = state.get("user_id", "")
    logger.info("[node_entry] name=enrich channel=%s", channel)

    # Rehydrate in-memory profile/transaction caches after a process restart.
    # The UI keeps its auth token in localStorage, so the user stays logged in
    # while the backend's `_profile_data` dict has been wiped. Without this
    # check, data tools return empty until the user manually logs out + back in.
    if user_id:
        from app.services import profile_service
        from app.services.transaction_service import load_transactions
        if not profile_service.is_loaded(user_id):
            try:
                profile_service.load_profile(user_id)
                prefix = profile_service.get_file_prefix(user_id)
                if prefix:
                    load_transactions(user_id, prefix)
                logger.info("[session_rehydrate] user_id=%s", user_id)
                m = current_turn_metrics()
                if m is not None:
                    m["rehydrated"] = True
            except Exception as e:  # noqa: BLE001
                logger.warning("[session_rehydrate_failed] user_id=%s err=%s", user_id, e)

    updates: dict = {
        "iteration_count": 0,
        "search_tool_calls": 0,
        "response_terminated": False,
        "knowledge_sources": [],  # reset per turn — Sources block reflects current turn only
        "last_executed_tools": [],  # reset per turn — response_instructions fire only right after a tool runs
        "variables": {},  # reset per turn — data-tool output slots for render tools
        "variables_order": {},  # reset per turn — slot → order number for rule-3 section ordering
        "variables_counter": 0,  # reset per turn — monotonic within-turn counter
        "go_to_presenter": False,  # reset per turn — set by sub-agent returns with to_presenter mode
        "hop_guard_triggered": False,  # reset per turn — set by hop_guard_fallback when two-phase cap hits
    }

    # Base system prompt: profile + memory aggregation is expensive; build once per session.
    if not state.get("base_system_prompt"):
        from app.services.enrichment import EnrichmentService
        from app.services.memory import MemoryService
        from app.database import get_session_context, get_chroma_client

        with get_session_context() as db_session:
            memory = MemoryService(db_session, get_chroma_client())
            enrichment = EnrichmentService(memory)
            base_prompt = enrichment.build_system_prompt(state["user_id"], state["session_id"])
        updates["base_system_prompt"] = base_prompt
    else:
        base_prompt = state["base_system_prompt"]

    # Rebuild always-loaded tool schemas every turn — knowledge_search.description()
    # injects a dynamic KB descriptor that may change mid-session.
    always_load = get_always_load_tools(channel)
    always_names = {t.name for t in always_load}
    fresh_schemas = [await t.to_openai_schema() for t in always_load]
    fresh_names = [t.name for t in always_load]

    # Preserve deferred tools that tool_search has already discovered.
    prev_names = state.get("available_tools", []) or []
    prev_schemas = state.get("tool_schemas", []) or []
    for n, s in zip(prev_names, prev_schemas):
        if n not in always_names:
            fresh_names.append(n)
            fresh_schemas.append(s)

    updates["available_tools"] = fresh_names
    updates["tool_schemas"] = fresh_schemas

    enriched = base_prompt
    if channel == "voice":
        enriched += (
            "\n\nVoice mode. These instructions govern responses you generate yourself "
            "(conversational turns without tool output). Respond in short spoken sentences — "
            "no markdown, lists, tables, or code. Tool outputs for voice are pre-formatted "
            "speech and are delivered to the user verbatim; do not describe, reformat, or "
            "summarize them."
        )

    updates["enrichment_context"] = enriched

    _m = current_turn_metrics()
    if _m is not None:
        _m["enrich_ms"] += (time.perf_counter() - _enrich_start) * 1000

    return updates


async def llm_call(state: AgentState) -> dict:
    """Call the LLM via llm_service. Injects workflow_instructions from bound tools."""
    from app.services.llm_service import get_llm

    logger.info(
        "[node_entry] name=planner_llm iteration=%d bound_tools=%d",
        state.get("iteration_count", 0) + 1,
        len(state.get("tool_schemas", []) or []),
    )

    llm = get_llm()

    # Build system message with dynamic workflow instructions
    system_content = state.get("enrichment_context", "You are a helpful financial assistant.")

    # Collect workflow_instructions from all currently bound tools
    instructions = []
    for tool_name in state.get("available_tools", []):
        tool = get_tool(tool_name)
        if tool and tool.workflow_instructions:
            instructions.append(tool.workflow_instructions.strip())

    if instructions:
        system_content += "\n\n" + "\n\n".join(instructions)

    # Per-tool response_instructions — fires only when the LLM gets a follow-up
    # iteration after tool_execute. Widget-emitting tools in chat terminate the
    # graph (response_terminated=True → END), so their response_instructions is
    # effectively voice-only guidance. Tools the LLM is expected to paraphrase
    # (knowledge_search, any non-widget tool) always see these instructions.
    last_exec = state.get("last_executed_tools", []) or []
    response_blocks = []
    for tool_name in last_exec:
        tool = get_tool(tool_name)
        if tool and tool.response_instructions:
            response_blocks.append(tool.response_instructions.strip())
    if response_blocks:
        system_content += "\n\n" + "\n\n".join(response_blocks)

    system = SystemMessage(content=system_content)
    # Phase-1 compaction: collapse large / old tool results so context
    # grows sub-linearly with conversation length.
    trimmed = _trim_messages_for_llm(list(state["messages"]))
    messages = [system] + trimmed

    tool_schemas = state.get("tool_schemas")
    if tool_schemas:
        llm_with_tools = llm.bind_tools(tool_schemas)
    else:
        llm_with_tools = llm

    _llm_start = time.perf_counter()
    response: AIMessage = await llm_with_tools.ainvoke(messages)
    _llm_ms = (time.perf_counter() - _llm_start) * 1000

    iteration = state["iteration_count"] + 1
    tc_names = [tc["name"] for tc in (response.tool_calls or [])]
    content_len = len(response.content) if isinstance(response.content, str) else 0

    # Token accounting — LangChain exposes OpenAI's usage via usage_metadata.
    # cache_read tells us whether OpenAI's automatic prompt cache fired
    # (critical for latency diagnosis on gateways that might strip cache
    # headers). response_metadata carries model + system_fingerprint so we
    # can see which backend actually served the call.
    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cache_read = ((usage.get("input_token_details") or {}).get("cache_read", 0)) or 0
    rmeta = getattr(response, "response_metadata", None) or {}
    model_name = rmeta.get("model_name") or rmeta.get("model") or "?"
    sys_fp = rmeta.get("system_fingerprint") or "-"
    logger.info(
        "[llm_call.v1] iteration=%d duration_ms=%.0f tool_calls=%s content_len=%d "
        "input_tokens=%d output_tokens=%d cached_tokens=%d model=%s fingerprint=%s",
        iteration, _llm_ms, tc_names, content_len,
        input_tokens, output_tokens, cache_read, model_name, sys_fp,
    )
    _m = current_turn_metrics()
    if _m is not None:
        _m["llm_calls"] += 1
        _m["llm_total_ms"] += _llm_ms
        _m["iterations"] = iteration
        # Any non-empty LLM content → prose was part of this turn's surface.
        # Two-phase turn-2 compound responses carry prose + present_widget in
        # the same AIMessage; this flag drives response_shape at turn_summary.
        if content_len > 0:
            _m["prose_emitted"] = True

    return {
        "messages": [response],
        "iteration_count": iteration,
    }


async def tool_execute(state: AgentState) -> dict:
    """Execute tool calls — concurrent for safe tools, sequential for unsafe."""
    _te_start = time.perf_counter()
    last_message = state["messages"][-1]
    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        return {}
    logger.info(
        "[node_entry] name=tool_execute tools=%s",
        [tc["name"] for tc in last_message.tool_calls],
    )

    new_available = list(state.get("available_tools", []))
    new_schemas = list(state.get("tool_schemas", []))
    search_count = state.get("search_tool_calls", 0)
    channel = state.get("channel", "chat")
    collected_sources = list(state.get("knowledge_sources", []) or [])
    seen_source_urls = {s.get("url") for s in collected_sources if s.get("url")}
    variables: dict = dict(state.get("variables", {}) or {})  # carries into context for render tools
    variables_order: dict = dict(state.get("variables_order", {}) or {})  # slot → turn-local order number
    variables_counter: int = int(state.get("variables_counter", 0) or 0)

    context = {
        "user_id": state["user_id"],
        "session_id": state["session_id"],
        "channel": channel,
        "available_tools": new_available,
        "search_tool_calls": search_count,
        "variables": variables,  # render tools look up <field>_slot here
    }

    terminated = False
    go_to_presenter = False

    async def run_one(tc, tool):
        """Execute a single tool call, return (ToolMessage, tool_name, raw_result, slot_data)."""
        nonlocal terminated, go_to_presenter
        slot_data = None
        _tc_start = time.perf_counter()
        # User-visible activity event — emitted for every NON-internal tool so
        # the chat UI can render a per-tool spinner with a friendly label.
        # Skip is_internal tools (present_widget, tool_search) — they're
        # orchestration plumbing, not user-meaningful work.
        show_activity = bool(tool and not getattr(tool, "is_internal", False))
        if show_activity:
            try:
                label = tool.activity_description(tc.get("args") or {})
            except Exception:
                label = f"Running {tc['name']}..."
            try:
                dispatch_custom_event("tool_activity", {
                    "phase": "start",
                    "name": tc["name"],
                    "args": tc.get("args") or {},
                    "label": label,
                })
            except Exception:
                # SSE side-channel. Callable outside a LangGraph run (tests,
                # unit harnesses) — don't fail the tool for telemetry.
                pass
        if not tool:
            llm_text = json.dumps({"error": f"Tool '{tc['name']}' not found"})
        else:
            try:
                result = await tool.execute(tc["args"], context)
                if isinstance(result, ToolResult):
                    # Voice channel suppresses widgets entirely; chat keeps widget behavior
                    if channel == "chat" and result.widget:
                        from app.database import get_session_context
                        from app.services.widget_service import WidgetService
                        # Stamp user + session context into widget metadata so
                        # widget-action handlers (transfer_form validate/submit,
                        # etc.) can re-enter the right service on a later POST.
                        meta = dict(result.widget.get("metadata") or {})
                        meta.setdefault("user_id", state.get("user_id", ""))
                        meta.setdefault("session_id", state.get("session_id", ""))
                        result.widget["metadata"] = meta
                        try:
                            with get_session_context() as db:
                                ws = WidgetService(db)
                                instance = ws.create_instance(
                                    session_id=state["session_id"],
                                    widget_data=result.widget,
                                    created_by=tc["name"],
                                )
                            widget_payload = {
                                **result.widget,
                                "instance_id": instance.id,
                                "status": "pending",
                            }
                        except Exception:
                            widget_payload = result.widget
                        dispatch_custom_event("widget", widget_payload)
                        terminated = True
                        _mm = current_turn_metrics()
                        if _mm is not None:
                            _mm["widget_emitted"] = True
                    elif result.glass:
                        dispatch_custom_event(
                            "final_response",
                            {"content": result.glass, "channel": channel},
                        )
                        terminated = True

                    if result.final:
                        terminated = True

                    # Sub-agent returning with return_mode=to_presenter:
                    # flag the outer graph to route into the Presenter
                    # instead of re-entering the Planner.
                    if getattr(result, "go_to_presenter", False):
                        go_to_presenter = True

                    for s in result.sources:
                        url = s.get("url")
                        if url and url not in seen_source_urls:
                            seen_source_urls.add(url)
                            collected_sources.append(s)

                    slot_data = result.slot_data
                    llm_text = result.to_llm
                else:
                    llm_text = str(result) if result else ""
            except Exception as e:
                # GraphBubbleUp (incl. GraphInterrupt) must propagate to the
                # LangGraph engine so sub-agents can pause for user input.
                from langgraph.errors import GraphBubbleUp
                if isinstance(e, GraphBubbleUp):
                    raise
                llm_text = json.dumps({"error": str(e)})

        if llm_text and len(llm_text) > _LARGE_TOOL_RESULT_CHARS:
            logger.warning(
                "[tool_result_size] %s returned %d chars (~%d tokens) to LLM",
                tc["name"], len(llm_text), len(llm_text) // 4,
            )

        # Per-tool duration log — separate from the [tool_execute.v1] batch
        # line so you can see which individual tool is slow (e.g., a KB query
        # vs a profile lookup in a parallel gather). Also flags `is_internal`
        # tools separately so they're visible in logs but easy to filter out.
        _tc_ms = (time.perf_counter() - _tc_start) * 1000
        _is_err = bool(llm_text and "error" in llm_text[:60].lower())
        logger.info(
            "[tool_call.v1] name=%s duration_ms=%.0f internal=%s error=%s output_len=%d",
            tc["name"], _tc_ms,
            bool(tool and getattr(tool, "is_internal", False)),
            _is_err, len(llm_text or ""),
        )

        if show_activity:
            try:
                dispatch_custom_event("tool_activity", {
                    "phase": "end",
                    "name": tc["name"],
                    "preview": (llm_text or "")[:200],
                })
            except Exception:
                pass

        return (
            ToolMessage(content=llm_text, tool_call_id=tc["id"]),
            tc["name"],
            llm_text,
            slot_data,
        )

    # Split into concurrent-safe and sequential
    concurrent_tasks = []
    sequential_calls = []

    for tc in last_message.tool_calls:
        tool = get_tool(tc["name"])
        if tool and tool.is_concurrency_safe:
            concurrent_tasks.append((tc, tool))
        else:
            sequential_calls.append((tc, tool))

    # Run concurrent tools in parallel
    results = []
    if concurrent_tasks:
        results.extend(
            await asyncio.gather(*(run_one(tc, tool) for tc, tool in concurrent_tasks))
        )

    # Run sequential tools one by one
    for tc, tool in sequential_calls:
        r = await run_one(tc, tool)
        results.append(r)

    # Process results
    tool_messages = []
    for msg, tool_name, raw_result, slot_data in results:
        tool_messages.append(msg)

        # Handle tool_search discovery
        if tool_name == "tool_search":
            search_count += 1
            try:
                discovered = json.loads(raw_result)
                if isinstance(discovered, list):
                    for d in discovered:
                        dname = d.get("name", "")
                        if dname and dname not in new_available:
                            discovered_tool = get_tool(dname)
                            if discovered_tool:
                                new_available.append(dname)
                                new_schemas.append(
                                    await discovered_tool.to_openai_schema()
                                )
            except (json.JSONDecodeError, TypeError):
                pass

        # Write data-tool output to state.variables slot.
        # Prefer ToolResult.slot_data (tool explicitly provided full payload)
        # over parsing to_llm, so tools can send a compact LLM summary while
        # preserving a render-ready dataset for the widget.
        tool_obj = get_tool(tool_name)
        if tool_obj and tool_obj.output_var:
            wrote_slot = False
            if slot_data is not None:
                variables[tool_obj.output_var] = slot_data
                wrote_slot = True
            elif raw_result:
                try:
                    parsed = json.loads(raw_result)
                    variables[tool_obj.output_var] = parsed
                    wrote_slot = True
                except (json.JSONDecodeError, TypeError):
                    variables[tool_obj.output_var] = raw_result
                    wrote_slot = True
                    logger.warning(
                        "[output_var_parse] %s wrote raw string to slot %s (JSON parse failed)",
                        tool_name, tool_obj.output_var,
                    )
            if wrote_slot:
                # Monotonic counter — serialized in results-iteration order
                # (submission order from the Planner's tool_calls), not async
                # completion order. Used by Presenter rule 3 for section ordering.
                variables_counter += 1
                variables_order[tool_obj.output_var] = variables_counter

    _te_ms = (time.perf_counter() - _te_start) * 1000
    tool_names = [tc["name"] for tc in last_message.tool_calls]
    logger.info(
        "[tool_execute.v1] tools=%s duration_ms=%.0f terminated=%s go_to_presenter=%s",
        tool_names, _te_ms, terminated, go_to_presenter,
    )
    _m = current_turn_metrics()
    if _m is not None:
        _m["tool_calls"] += len(tool_names)
        _m["tool_total_ms"] += _te_ms
        _m["tool_names"].extend(tool_names)
        # Record this phase's tools so turn_summary can compute
        # turn2_tool_delta / turn2_tool_repeat across phases. One phase =
        # one Planner LLM call + its tool_execute. Uses Planner's intended
        # tool_calls (submission order) rather than completion order.
        _m["tool_names_by_phase"].append(list(tool_names))
        # widget_emitted is flagged at the dispatch_custom_event("widget")
        # sites in run_one() (sub-agent widget path) and in presenter() —
        # closer to the action than inferring from terminated flag.

    return {
        "messages": tool_messages,
        "available_tools": new_available,
        "tool_schemas": new_schemas,
        "search_tool_calls": search_count,
        "response_terminated": terminated,
        "go_to_presenter": go_to_presenter,
        "knowledge_sources": collected_sources,
        "last_executed_tools": [tc["name"] for tc in last_message.tool_calls],
        "variables": variables,
        "variables_order": variables_order,
        "variables_counter": variables_counter,
    }


# --- Router ---
# The Planner/Presenter/fast-path routing lives below. Two routers:
#   - should_route runs right after planner_llm. It cannot check slot population
#     (tool_execute hasn't run yet), so it only distinguishes "text" vs "has
#     tool calls" vs "max iterations."
#   - post_tool_router runs after tool_execute. It sees response_terminated and
#     state.variables, so it can pick fast_path_synth / presenter / planner_llm.
# See backend/docs/widget_architecture.md for the full graph shape.


def should_route(state: AgentState) -> str:
    """Route after the Planner turn.

    Returns one of:
      - text_fast_path — no tool calls + content (terminal)
      - tool_execute   — Planner emitted tool calls; run them first, then
                         post_tool_router decides where to go next
      - end            — max iterations reached
    """
    # Max iterations guard
    if state.get("iteration_count", 0) >= settings.max_agent_iterations:
        logger.info("[router_decision] branch=end reason=max_iterations_reached")
        return "end"

    last_message = state["messages"][-1]
    has_tool_calls = (
        hasattr(last_message, "tool_calls")
        and last_message.tool_calls
        and len(last_message.tool_calls) > 0
    )

    if not has_tool_calls:
        content = getattr(last_message, "content", "") or ""
        logger.info("[router_decision] branch=text_fast_path content_len=%d", len(str(content)))
        return "text_fast_path"

    # Log which shape the Planner turn has so we know why post_tool_router decides
    tool_calls = last_message.tool_calls
    names = [tc["name"] for tc in tool_calls]
    logger.info("[router_decision] branch=tool_execute planner_tools=%s", names)
    return "tool_execute"


# Two-phase hop cap. The Planner gets at most TWO Planner LLM calls per user
# turn without emitting present_widget(). After the second phase's
# tool_execute, if the Planner still hasn't signalled (no present_widget, no
# termination), we force-terminate via hop_guard_fallback rather than looping
# into a third Planner call. Adjust in followups ONLY after production logs
# show the Planner legitimately wanting a third hop (see hop_guard_triggered
# observability).
_TWO_PHASE_HOP_CAP = 2


def post_tool_router(state: AgentState) -> str:
    """After tool_execute has run the Planner's tool_calls. Decides the next hop.

    Branches:
      - end              — a tool already emitted a terminal signal (sub-agent
                           widget, glass, final).
      - presenter        — Planner called present_widget() in its most recent
                           AIMessage. Presenter runs deterministic rules,
                           emits the widget, ends.
      - hop_guard_fallback — two-phase cap hit: iteration >= 2 and no
                           present_widget and not terminated.
      - planner_llm      — otherwise; continue the ReAct loop (data tools only).
    """
    # 1. Sub-agent widget, glass, or final → graph ends.
    if state.get("response_terminated"):
        logger.info("[router_decision] branch=end reason=response_terminated")
        return "end"

    # 2. Sub-agent returned with return_mode=to_presenter → run Presenter
    #    on the slot_writes the sub-agent populated into state.variables.
    if state.get("go_to_presenter"):
        logger.info("[router_decision] branch=presenter reason=go_to_presenter")
        return "presenter"

    # 3. Did the Planner hand off via present_widget()?
    from app.agent.presenter import _last_planner_turn
    planner_turn = _last_planner_turn(state)
    if planner_turn and any(tc["name"] == "present_widget" for tc in (planner_turn.tool_calls or [])):
        logger.info("[router_decision] branch=presenter")
        return "presenter"

    # 4. Two-phase hop guard. We've already done `_TWO_PHASE_HOP_CAP` Planner
    #    calls and the most recent one is asking for more tool work without
    #    a `present_widget()` handoff — the Planner is either stalling or
    #    trying to narrate-then-loop. Cut the loop here and fall back.
    if state.get("iteration_count", 0) >= _TWO_PHASE_HOP_CAP:
        intended = [tc["name"] for tc in (planner_turn.tool_calls or [])] if planner_turn else []
        content_len = 0
        if planner_turn and isinstance(getattr(planner_turn, "content", None), str):
            content_len = len(planner_turn.content)
        logger.info(
            "[hop_guard_triggered] iteration=%d intended_tools=%s content_len=%d "
            "(narrate-and-loop if content_len>0; stall if content_len==0)",
            state.get("iteration_count", 0), intended, content_len,
        )
        _m = current_turn_metrics()
        if _m is not None:
            _m["hop_guard_triggered"] = True
            _m["hop_guard_intended_tools"] = list(intended)
            _m["hop_guard_content_len"] = content_len
        logger.info("[router_decision] branch=hop_guard_fallback")
        return "hop_guard_fallback"

    # 5. Keep gathering.
    logger.info("[router_decision] branch=planner_llm")
    return "planner_llm"


# --- Hop guard fallback node ---


async def hop_guard_fallback(state: AgentState) -> dict:  # noqa: D401
    """Force-terminate the turn with a generic text_card widget when the
    two-phase cap is hit."""
    logger.info("[node_entry] name=hop_guard_fallback")
    return await _hop_guard_fallback_impl(state)


async def _hop_guard_fallback_impl(state: AgentState) -> dict:
    """Force-terminate the turn with a generic text_card widget when the
    two-phase cap is hit. Dispatches the widget via the same SSE channel as
    Presenter-emitted widgets, sets response_terminated=True, and ends.

    Sets hop_guard_triggered=True in state so the turn_summary line carries
    the signal. (The metrics ContextVar flag is already set in post_tool_router
    — we also stamp state for checkpointer-side observability.)
    """
    import json
    channel = state.get("channel", "chat")
    if channel == "chat":
        from app.widgets.builders import text_card_widget
        widget = json.loads(text_card_widget(
            "I'm having trouble synthesizing a complete answer from the "
            "available data. Could you rephrase or narrow what you're asking?",
            title="Sorry — I got stuck",
        ))
        # Persist so the frontend sees the same envelope shape as any other widget.
        try:
            from app.database import get_session_context
            from app.services.widget_service import WidgetService
            with get_session_context() as db:
                ws = WidgetService(db)
                instance = ws.create_instance(
                    session_id=state["session_id"],
                    widget_data=widget,
                    created_by="hop_guard_fallback",
                )
            widget = {**widget, "instance_id": instance.id, "status": "pending"}
        except Exception:
            logger.warning("[hop_guard_fallback_persist_error]", exc_info=True)
        dispatch_custom_event("widget", widget)
        _m = current_turn_metrics()
        if _m is not None:
            _m["widget_emitted"] = True
    else:
        # Voice: emit a short prose apology via the final_response channel.
        dispatch_custom_event(
            "final_response",
            {
                "content": "Sorry — I'm having trouble putting together a "
                           "complete answer right now. Could you rephrase?",
                "channel": channel,
            },
        )
    return {"response_terminated": True, "hop_guard_triggered": True}



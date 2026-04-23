"""Presenter node — deterministic rules engine for widget rendering.

Given accumulated state.variables, select_render() picks ONE RenderDecision
using four rules over catalog metadata. No LLM involved. The presenter() node
invokes the selector, calls the builder, persists the widget, dispatches the
SSE event, and terminates the graph.

Rules (first match wins):
  1. Designed composite — populated slots exactly match some catalog entry's
     slot_combination. Tiebreaker: catalog declaration order.
  2. Single slot — exactly one populated slot has a default_data_var →
     that widget.
  3. Generic composite — 2+ populated composable mapped slots → render_
     generic_composite with up to 3 sections, sorted by composite_priority
     then population order.
  4. Text-card fallback — everything else → text_card with a mechanical
     summary derived from widget_to_llm per populated slot.

Graph contract:
- Invoked only when post_tool_router sees present_widget() in the Planner's
  most recent tool_calls AND response_terminated is not already True.
- Terminates the graph directly (edge to END). Sets response_terminated=True
  for downstream consumer uniformity.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Callable

from langchain_core.callbacks import dispatch_custom_event
from langchain_core.messages import AIMessage

from app.agent.state import AgentState
from app.widgets.builders import text_card_widget, generic_composite_widget
from app.widgets.catalog import WIDGET_CATALOG, _DECLARATION_INDEX

logger = logging.getLogger(__name__)


# --- Data classes ---


@dataclass(frozen=True)
class RenderDecision:
    rule: str                 # "designed_composite" | "single_slot" | "generic_composite" | "text_card_fallback"
    widget_type: str          # catalog key (or "text_card" for rule 4)
    build: Callable           # builder function — returns a JSON string
    build_args: dict = field(default_factory=dict)
    slots_used: tuple[str, ...] = ()   # populated slots this decision drew from (for logging)


# --- Helpers ---


def _declaration_index(widget_type: str) -> int:
    return _DECLARATION_INDEX[widget_type]


def _population_order(state: AgentState, slot: str) -> int:
    order = state.get("variables_order") or {}
    return order.get(slot, 0)


def _last_planner_turn(state: AgentState) -> AIMessage | None:
    """Most recent Planner AIMessage in the message history.

    Returns the latest AIMessage that wasn't emitted by the Presenter
    (defensive — current Presenter adds no messages) and isn't a legacy
    fast_path_synth message. May be from a prior turn if the current turn's
    AIMessage has no `content` of interest to callers.
    """
    for msg in reversed(state.get("messages") or []):
        if not isinstance(msg, AIMessage):
            continue
        # Defensive: current Presenter adds no AIMessages, but this filter
        # protects _last_planner_turn if a future Presenter version does.
        if getattr(msg, "name", None) == "presenter":
            continue
        # Back-compat: graphs checkpointed before v8 cutover may contain
        # fast_path_synth-emitted tool_calls. TODO: remove after 30-day
        # checkpointer retention window elapses post-cutover.
        if any((tc.get("id") or "").startswith("fast_path_") for tc in (msg.tool_calls or [])):
            continue
        return msg
    return None


def _last_planner_ai_content(state: AgentState) -> str:
    """Content of the most recent Planner AIMessage. May be from any turn.
    Does NOT walk backwards past the latest Planner message — if that
    message has empty content, this returns empty."""
    msg = _last_planner_turn(state)
    return msg.content if msg and isinstance(msg.content, str) else ""


def _planner_content_turn_distance(state: AgentState) -> int:
    """How many human-message boundaries back is the latest Planner AIMessage?
    0 = current turn. Used for [presenter_title_source] observability."""
    from langchain_core.messages import HumanMessage
    distance = 0
    seen_planner = False
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, AIMessage) and not seen_planner:
            if getattr(msg, "name", None) == "presenter":
                continue
            if any((tc.get("id") or "").startswith("fast_path_") for tc in (msg.tool_calls or [])):
                continue
            seen_planner = True
            continue
        if isinstance(msg, HumanMessage) and seen_planner:
            distance += 1
    return distance


# --- Rule builder arg helpers ---


def _build_single_slot_args(entry: dict, slot: str, variables: dict) -> dict:
    """Rule 2 — build kwargs for a single-slot render_fn.

    slot_arg_map is required (enforced by catalog validation); no identity
    fallback. KeyError here means the catalog validation was bypassed.
    """
    mapping = entry["slot_arg_map"]
    return {mapping[slot]: variables[slot]}


def _build_composite_args(entry: dict, variables: dict) -> dict:
    """Rule 1 — build kwargs for a designed composite render_fn."""
    mapping = entry["slot_arg_map"]
    return {mapping[s]: variables[s] for s in entry["slot_combination"]}


# --- Rule 4 fallback content ---


def _fallback_content(state: AgentState, populated: set[str], mapped_slots: dict) -> str:
    """Assemble text_card content mechanically — no LLM call.

    Precedence:
      1. Planner's most recent AIMessage content (if non-empty).
      2. Per-slot widget_to_llm summaries joined with newlines.
      3. Literal "I didn't find anything to show."
    """
    planner_prose = _last_planner_ai_content(state).strip()
    if planner_prose:
        return planner_prose

    if not populated:
        return "I didn't find anything to show."

    from app.widgets.summarizers import widget_to_llm

    variables = state.get("variables") or {}
    lines: list[str] = []
    for slot in sorted(populated):
        value = variables.get(slot)
        if slot in mapped_slots:
            widget_type, _ = mapped_slots[slot]
            fake_widget = {"widget": widget_type, "data": value}
            lines.append(widget_to_llm(fake_widget))
        else:
            lines.append(f"{slot}: {_short_repr(value)}")
    return "\n".join(lines) or "I gathered the following."


def _short_repr(value) -> str:
    if isinstance(value, list):
        return f"list of {len(value)} item(s)"
    if isinstance(value, dict):
        return f"dict with {len(value)} key(s)"
    s = str(value)
    return s[:80] + ("…" if len(s) > 80 else "")


# --- Near-miss observability (rule 1) ---


def _log_near_miss(populated: set[str]) -> None:
    """Log if populated slots ⊃ some composite's slot_combination by exactly
    one extra slot. Signals "designed composite almost fired but didn't."
    """
    for wt, entry in WIDGET_CATALOG.items():
        combo = set(entry.get("slot_combination") or ())
        if combo and combo.issubset(populated) and len(populated) - len(combo) == 1:
            extra = populated - combo
            logger.info(
                "[presenter_designed_composite_missed] populated=%s closest=%s extras=%s",
                sorted(populated), wt, sorted(extra),
            )
            return


# --- Rule engine ---


def select_render(state: AgentState) -> RenderDecision:
    """Pure function over state. Returns a RenderDecision.

    state may be a live AgentState (production) or a synthesized dict with
    the same keys (tests). The function reads variables, variables_order,
    and messages (via _last_planner_ai_content for rule 3 title / rule 4
    fallback content).
    """
    variables: dict = state.get("variables") or {}
    populated: set[str] = {k for k, v in variables.items() if v}

    mapped_slots: dict[str, tuple[str, dict]] = {
        entry["default_data_var"]: (wt, entry)
        for wt, entry in WIDGET_CATALOG.items()
        if entry.get("default_data_var") and entry.get("render_fn")
    }
    populated_mapped: set[str] = populated & mapped_slots.keys()

    # --- Rule 1: designed composite (exact set match) ---
    exact_matches = [
        (wt, entry) for wt, entry in WIDGET_CATALOG.items()
        if entry.get("slot_combination") and set(entry["slot_combination"]) == populated
    ]
    if exact_matches:
        exact_matches.sort(key=lambda x: _declaration_index(x[0]))
        wt, entry = exact_matches[0]
        return RenderDecision(
            rule="designed_composite",
            widget_type=wt,
            build=entry["render_fn"],
            build_args=_build_composite_args(entry, variables),
            slots_used=tuple(entry["slot_combination"]),
        )

    # --- Rule 2: single mapped slot ---
    if len(populated_mapped) == 1:
        slot = next(iter(populated_mapped))
        wt, entry = mapped_slots[slot]
        return RenderDecision(
            rule="single_slot",
            widget_type=wt,
            build=entry["render_fn"],
            build_args=_build_single_slot_args(entry, slot, variables),
            slots_used=(slot,),
        )

    # --- Rule 3: generic composite (2+ composable mapped slots) ---
    composable = [
        (slot, mapped_slots[slot]) for slot in populated_mapped
        if mapped_slots[slot][1].get("composable") != "never"
    ]
    if len(composable) >= 2:
        # Log near-miss if a designed composite was within one extra slot.
        _log_near_miss(populated)

        composable.sort(key=lambda x: (
            x[1][1].get("composite_priority", 100),
            _population_order(state, x[0]),
        ))
        if len(composable) > 3:
            logger.info(
                "[presenter_truncate] rule=generic_composite total=%d keeping=3 dropped=%s",
                len(composable), [c[0] for c in composable[3:]],
            )
            composable = composable[:3]

        sections = [
            {"widget_type": wt, "data": variables[slot]}
            for slot, (wt, _) in composable
        ]
        title = _last_planner_ai_content(state).strip() or ""
        if title:
            distance = _planner_content_turn_distance(state)
            logger.info(
                "[presenter_title_source] turn_distance=%d title_len=%d",
                distance, len(title),
            )
        return RenderDecision(
            rule="generic_composite",
            widget_type="generic_composite",
            build=generic_composite_widget,
            build_args={"sections": sections, "title": title},
            slots_used=tuple(slot for slot, _ in composable),
        )

    # --- Rule 4: text-card fallback ---
    return RenderDecision(
        rule="text_card_fallback",
        widget_type="text_card",
        build=text_card_widget,
        build_args={
            "content": _fallback_content(state, populated, mapped_slots),
            "title": "Results",
        },
        slots_used=tuple(sorted(populated)),
    )


# --- Node: build + emit + terminate ---


def _persist_and_stamp(state: AgentState, widget: dict, created_by: str) -> dict:
    """Persist the widget in the DB and stamp it with an instance_id + status.

    Matches the behavior in tool_execute's widget-emission path so the frontend
    sees the same envelope shape regardless of which node emitted the widget.
    """
    try:
        from app.database import get_session_context
        from app.services.widget_service import WidgetService
        with get_session_context() as db:
            ws = WidgetService(db)
            instance = ws.create_instance(
                session_id=state["session_id"],
                widget_data=widget,
                created_by=created_by,
            )
        return {**widget, "instance_id": instance.id, "status": "pending"}
    except Exception:
        logger.warning("[presenter_persist_error] falling back to un-stamped widget", exc_info=True)
        return widget


async def presenter(state: AgentState) -> dict:
    """Terminal widget-emission node. Rules → build → emit → end.

    Does not call the LLM. Does not add AIMessages to the message history.
    Sets response_terminated=True for downstream-consumer uniformity.
    """
    import time
    from app.agent.nodes import current_turn_metrics
    _p_start = time.perf_counter()
    channel = state.get("channel", "chat")
    logger.info(
        "[node_entry] name=presenter channel=%s populated_slots=%s",
        channel, sorted((state.get("variables") or {}).keys()),
    )

    # Voice channel should not have reached here — present_widget is chat-only.
    # Defensive: suppress widget, return no-op.
    if channel != "chat":
        logger.warning("[presenter] invoked in channel=%s; suppressing widget", channel)
        return {"response_terminated": True}

    decision = select_render(state)

    try:
        widget_json = decision.build(**decision.build_args)
        widget = json.loads(widget_json) if isinstance(widget_json, str) else widget_json
    except Exception as e:
        logger.warning(
            "[presenter_error] rule=%s widget=%s error=%s — falling back to text_card",
            decision.rule, decision.widget_type, e,
        )
        widget = json.loads(text_card_widget(
            "I hit a snag formatting that result.",
            title="Sorry",
        ))

    stamped = _persist_and_stamp(state, widget, created_by=f"presenter:{decision.rule}")
    dispatch_custom_event("widget", stamped)

    _p_ms = (time.perf_counter() - _p_start) * 1000
    logger.info(
        "[presenter_choice] rule=%s widget=%s slots=%s duration_ms=%.0f",
        decision.rule, decision.widget_type, list(decision.slots_used), _p_ms,
    )
    _m = current_turn_metrics()
    if _m is not None:
        _m["presenter_ms"] += _p_ms
        _m["widget_emitted"] = True

    return {"response_terminated": True}

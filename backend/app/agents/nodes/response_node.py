"""response_node — terminal node. Emits output via one of four return modes.

Sets state._terminal = True. The runtime's cleanup logic reads this on
next sub-agent invocation: terminal → fresh start (clear-on-entry
semantics, avoids state race between sessions).

Data schema:
  return_mode: "widget" | "glass" | "to_presenter" | "to_orchestrator"

  # widget mode — two paths (see backend/docs/widget_response_node_migration.md):
    widget:
      widget_type: str

      # Position-2 path (preferred). Calls the catalog builder.
      kwargs: dict | null         # state paths → builder kwargs; values may contain {{var}}
      on_missing_required: "error" | "fallback_text"   # default: "error"
      fallback_text: str | null   # required when on_missing_required == "fallback_text"

      # Legacy path (deprecated; kept while templates migrate).
      data_template: dict | null  # hand-rolled widget.data shape
      title: str | null           # legacy only — Position-2 moves title under kwargs

      # Both paths.
      actions: [dict] | null      # honoured by legacy; ignored on new path
      metadata: dict | null       # free-form annotation, mergeable on new path

  # glass mode:
    glass_template: str       # TTS-ready text, {{var}}-resolvable

  # to_presenter mode:
    slot_writes: dict         # flat dict {main_slot: "{{sub.variable}}"}
    # Regulated templates CANNOT use this mode (enforced in loader).

  # to_orchestrator mode:
    text_template: str        # prose the Planner will paraphrase
    text_source: "template" | "last_assistant_message"   # default: template
    # When text_source == "last_assistant_message", the node ignores
    # text_template and returns the .content of the most recent AIMessage
    # in state.messages. Use this when a prior llm_node already produced
    # the final user-facing text (e.g. an inner ReAct loop's summary turn).
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from app.agents.nodes import register_node_type

logger = logging.getLogger(__name__)


_VALID_MODES = ("widget", "glass", "to_presenter", "to_orchestrator")
_FALLBACK_MODES = ("error", "fallback_text")


def _is_empty(v) -> bool:
    """§4.3.1 missing-value rule: None, empty/whitespace string, empty list/dict count as missing."""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def build_response_node_factory(data: dict) -> Callable:
    return_mode = data.get("return_mode", "to_orchestrator")
    if return_mode not in _VALID_MODES:
        raise ValueError(
            f"response_node.return_mode must be one of {_VALID_MODES}, got {return_mode!r}"
        )

    widget_cfg = data.get("widget") or {}
    glass_template = data.get("glass_template", "")
    slot_writes = data.get("slot_writes") or {}
    text_template = data.get("text_template", "")
    text_source = data.get("text_source", "template")
    if text_source not in ("template", "last_assistant_message"):
        raise ValueError(
            "response_node.text_source must be 'template' or 'last_assistant_message', "
            f"got {text_source!r}"
        )

    on_missing_required = widget_cfg.get("on_missing_required", "error")
    if return_mode == "widget" and on_missing_required not in _FALLBACK_MODES:
        raise ValueError(
            f"response_node.widget.on_missing_required must be one of {_FALLBACK_MODES}, "
            f"got {on_missing_required!r}"
        )

    node_label = data.get("label") or "?"

    async def handler(state: dict) -> dict:
        from app.utils.templates import resolve_templates

        result: dict = {"_terminal": True}

        if return_mode == "widget":
            variables = _emit_widget(state, widget_cfg, on_missing_required, node_label, resolve_templates)
            result["variables"] = variables

        elif return_mode == "glass":
            glass = str(resolve_templates(glass_template, state))
            variables = dict(state.get("variables") or {})
            variables["_response_glass"] = glass
            variables["_return_mode"] = "glass"
            result["variables"] = variables

        elif return_mode == "to_presenter":
            resolved_writes = {
                k: resolve_templates(v, state) for k, v in slot_writes.items()
            }
            variables = dict(state.get("variables") or {})
            variables["_response_slot_writes"] = resolved_writes
            variables["_return_mode"] = "to_presenter"
            result["variables"] = variables

        else:  # to_orchestrator
            if text_source == "last_assistant_message":
                text = _last_ai_content(state)
            else:
                text = str(resolve_templates(text_template, state))
            variables = dict(state.get("variables") or {})
            variables["_response_text"] = text
            variables["_return_mode"] = "to_orchestrator"
            result["variables"] = variables

        logger.info(
            "[subagent_response.v1] return_mode=%s terminal=true",
            return_mode,
        )
        return result

    return handler


def _last_ai_content(state: dict) -> str:
    from langchain_core.messages import AIMessage
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content.strip():
            return msg.content
    return ""


def _emit_widget(
    state: dict,
    widget_cfg: dict,
    on_missing_required: str,
    node_label: str,
    resolve_templates,
) -> dict:
    """Dispatch widget emission per §4.3 dual-path + §4.3.1 fallback."""
    from app.config import settings
    from app.widgets.catalog import WIDGET_CATALOG

    widget_type = widget_cfg.get("widget_type") or ""
    entry = WIDGET_CATALOG.get(widget_type)
    if entry is None or not callable(entry.get("render_fn")):
        raise ValueError(
            f"response_node {widget_type!r}: unknown widget_type or no render_fn in catalog"
        )

    raw_kwargs = widget_cfg.get("kwargs")
    has_kwargs = raw_kwargs is not None  # `{}` opts in; absent/None opts out
    if has_kwargs and not isinstance(raw_kwargs, dict):
        raise ValueError(
            f"response_node {widget_type!r}: widget.kwargs must be a dict, got {type(raw_kwargs).__name__}"
        )

    take_new_path = has_kwargs and bool(getattr(settings, "feature_response_node_builder", True))

    if not take_new_path:
        if widget_cfg.get("data_template") is None:
            raise ValueError(
                f"response_node {widget_type!r}: neither widget.kwargs nor "
                f"widget.data_template provided"
            )
        logger.warning(
            "[response_node_widget_legacy] node=%s widget=%s: using hand-rolled "
            "data_template — migrate to widget.kwargs (see widget_response_node_migration.md)",
            node_label, widget_type,
        )
        widget = {
            "widget": widget_type,
            "title": resolve_templates(widget_cfg.get("title") or "", state),
            "data": resolve_templates(widget_cfg.get("data_template") or {}, state),
            "actions": widget_cfg.get("actions") or [],
            "metadata": widget_cfg.get("metadata") or {},
        }
        variables = dict(state.get("variables") or {})
        variables["_response_widget"] = widget
        variables["_return_mode"] = "widget"
        return variables

    # New path: resolve kwargs, check required, call builder.
    resolved_kwargs = {k: resolve_templates(v, state) for k, v in raw_kwargs.items()}

    required_field_names = {
        f["name"] for f in (entry.get("fields") or []) if f.get("required") is True
    }
    missing = sorted(
        name for name in required_field_names
        if _is_empty(resolved_kwargs.get(name))
    )
    if missing:
        if on_missing_required == "fallback_text":
            fallback_template = widget_cfg.get("fallback_text") or ""
            fallback = str(resolve_templates(fallback_template, state))
            if not fallback.strip():
                raise ValueError(
                    f"response_node {widget_type!r}: on_missing_required=fallback_text "
                    f"but fallback_text is empty/missing"
                )
            logger.warning(
                "[response_node_widget_fallback] node=%s widget=%s missing=%s",
                node_label, widget_type, missing,
            )
            variables = dict(state.get("variables") or {})
            variables["_response_text"] = fallback
            variables["_return_mode"] = "to_orchestrator"
            return variables
        raise ValueError(
            f"response_node {widget_type!r}: required kwargs missing {missing}. "
            f"Either populate them upstream (parse_node, tool_call_node, llm_node) "
            f"or set widget.on_missing_required=\"fallback_text\" with a fallback_text template."
        )

    try:
        widget = json.loads(entry["render_fn"](**resolved_kwargs))
    except TypeError as e:
        raise ValueError(
            f"response_node {widget_type!r}: builder rejected kwargs "
            f"({sorted(resolved_kwargs)}): {e}"
        )

    # Templates may merge into `metadata`; they may NOT override `actions`.
    if widget_cfg.get("metadata"):
        widget = {**widget, "metadata": {**(widget.get("metadata") or {}), **widget_cfg["metadata"]}}
    if widget_cfg.get("actions"):
        logger.warning(
            "[response_node_widget_actions_ignored] node=%s widget=%s: "
            "template tried to override builder actions; ignoring (actions are "
            "part of the dispatch contract).",
            node_label, widget_type,
        )

    variables = dict(state.get("variables") or {})
    variables["_response_widget"] = widget
    variables["_return_mode"] = "widget"
    return variables


register_node_type("response_node", build_response_node_factory)

"""response_node — terminal node. Emits output via one of four return modes.

Sets state._terminal = True. The runtime's cleanup logic reads this on
next sub-agent invocation: terminal → fresh start (clear-on-entry
semantics, avoids state race between sessions).

Data schema:
  return_mode: "widget" | "glass" | "to_presenter" | "to_orchestrator"

  # widget mode:
    widget:
      widget_type: str
      data_template: dict     # values may contain {{var}}
      title: str | null
      actions: [dict] | null  # action definitions for interactive widgets

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

import logging
from typing import Callable

from app.agents.nodes import register_node_type

logger = logging.getLogger(__name__)


_VALID_MODES = ("widget", "glass", "to_presenter", "to_orchestrator")


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

    async def handler(state: dict) -> dict:
        from app.utils.templates import resolve_templates

        result: dict = {"_terminal": True}

        if return_mode == "widget":
            widget = {
                "widget": widget_cfg.get("widget_type", ""),
                "title": resolve_templates(widget_cfg.get("title") or "", state),
                "data": resolve_templates(widget_cfg.get("data_template") or {}, state),
                "actions": widget_cfg.get("actions") or [],
                "metadata": widget_cfg.get("metadata") or {},
            }
            variables = dict(state.get("variables") or {})
            variables["_response_widget"] = widget
            variables["_return_mode"] = "widget"
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


register_node_type("response_node", build_response_node_factory)

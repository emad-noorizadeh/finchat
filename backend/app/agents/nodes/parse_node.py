"""parse_node — extract values from the latest user message into variables.

Two modes:
  - regex: run named parsers from app/agents/parsers/ against the utterance.
  - llm:   one structured-output LLM call; multi-field JSON extraction.

Writes are MERGED into state.variables with null-means-unchanged semantics.
Updates retry tracking (§2): if `last_prompted_slot` didn't change after
this parse, increment parse_retry_count[slot]. After 3 consecutive
non-advancing parses, runtime sets retry_exhausted_for_slot — dispatcher's
priority-0 edge handles escalation.

Data schema:
  mode: "regex" | "llm"
  source: "last_user_message"     (only source supported in Phase 1)
  # regex mode:
    extractors: [
      {slot: "amount", parser: "money"},
      {slot: "confirmed", parser: "yes_no"},
      ...
    ]
  # llm mode:
    system_prompt: str
    output_schema: {field_name: {type: ..., nullable: true}, ...}
    writes: {field_name: variable_name}   # optional; defaults to identity (#11)
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.nodes import register_node_type
from app.agents.parsers import get_parser, llm_parse

logger = logging.getLogger(__name__)


_MAX_RETRIES_PER_SLOT = 3


def build_parse_node_factory(data: dict) -> Callable:
    mode = data.get("mode", "regex")
    source = data.get("source", "last_user_message")

    if mode == "regex":
        extractors = tuple(data.get("extractors") or ())
        return _build_regex_handler(source, extractors)

    if mode == "llm":
        system_prompt = data.get("system_prompt", "")
        output_schema = data.get("output_schema") or {}
        writes_raw = data.get("writes")
        # #11 — default writes map: identity. {field: field} for every schema key.
        writes = writes_raw if writes_raw else {k: k for k in output_schema.keys()}
        llm_variant = data.get("llm_variant", "sub_agent")
        return _build_llm_handler(source, system_prompt, output_schema, writes, llm_variant)

    raise ValueError(f"parse_node.mode must be 'regex' or 'llm', got {mode!r}")


def _latest_user_utterance(state: dict) -> str:
    """Walk messages backward, return the most recent HumanMessage content."""
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            return msg.content
    return ""


def _build_regex_handler(source: str, extractors: tuple[dict, ...]) -> Callable:
    async def handler(state: dict) -> dict:
        utterance = _latest_user_utterance(state)
        variables = dict(state.get("variables") or {})
        written: set[str] = set()

        for ext in extractors:
            slot = ext.get("slot")
            parser_name = ext.get("parser")
            parser = get_parser(parser_name) if parser_name else None
            if parser is None or not slot:
                continue
            try:
                value = parser(utterance, {"state": state})
            except Exception:  # noqa: BLE001
                value = None
            if value is not None:
                variables[slot] = value
                written.add(slot)

        return _apply_retry_tracking(state, variables, written)

    return handler


def _build_llm_handler(
    source: str,
    system_prompt: str,
    output_schema: dict,
    writes: dict,
    llm_variant: str,
) -> Callable:
    async def handler(state: dict) -> dict:
        utterance = _latest_user_utterance(state)
        variables = dict(state.get("variables") or {})
        written: set[str] = set()

        if not utterance:
            return _apply_retry_tracking(state, variables, written)

        parsed = await llm_parse(
            utterance,
            system_prompt=system_prompt,
            output_schema=output_schema,
            channel=state.get("channel", "chat"),
            llm_variant=llm_variant,
        )
        for field, variable in writes.items():
            value = parsed.get(field)
            if value is not None:
                variables[variable] = value
                written.add(variable)

        return _apply_retry_tracking(state, variables, written)

    return handler


def _apply_retry_tracking(state: dict, variables: dict, written: set[str]) -> dict:
    """Runtime safety (§2): track whether the targeted slot advanced.

    If the last interrupt targeted a specific slot, and this parse didn't
    fill/change that slot, increment the retry counter. At 3, set
    retry_exhausted_for_slot — dispatcher's priority-0 edge escalates.
    """
    last_slot = state.get("last_prompted_slot")
    retry_counts = dict(state.get("parse_retry_count") or {})
    retry_exhausted = state.get("retry_exhausted_for_slot")

    update: dict = {"variables": variables}

    if last_slot and last_slot not in written:
        retry_counts[last_slot] = retry_counts.get(last_slot, 0) + 1
        if retry_counts[last_slot] >= _MAX_RETRIES_PER_SLOT:
            retry_exhausted = last_slot
            logger.info(
                "[subagent_retry_exhausted.v1] slot=%s retries=%s",
                last_slot, retry_counts[last_slot],
            )
        update["parse_retry_count"] = retry_counts
        update["retry_exhausted_for_slot"] = retry_exhausted
    elif last_slot and last_slot in written:
        # Progress — clear this slot's retry count and any pending exhaustion.
        retry_counts.pop(last_slot, None)
        update["parse_retry_count"] = retry_counts
        if retry_exhausted == last_slot:
            update["retry_exhausted_for_slot"] = None

    return update


register_node_type("parse_node", build_parse_node_factory)

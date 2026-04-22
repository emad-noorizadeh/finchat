"""interrupt_node — signal a pause for the outer driver.

On entry: resolve {{var}} in prompt_template, set
`variables._pending_interrupt_payload`, store `last_prompted_slot`, and
return. The compiler short-circuits every interrupt_node's outgoing edge
to END so the inner graph terminates cleanly — the outer driver then
reads the payload, calls LangGraph's `interrupt()` at the outer level,
and on resume re-enters with the user's reply appended as a HumanMessage.

Rationale: the inner graph has no checkpointer. Calling LangGraph's
interrupt() inside would raise with no replay context. Pausing via a
state flag lets the OUTER graph's checkpointer handle all replay —
simpler and correct, at the cost of re-running the inner graph's pure
nodes on every outer turn (dispatcher re-reads accumulated variables
and skips completed work).

Data schema:
  prompt_template: str                 # {{var}}-resolvable prompt text
  voice_prompt_template: str | null    # voice override (channel-aware)
  targets_slot: str | null             # which slot this interrupt targets
  on_retry_exhausted: "<node_id>" | null   # override runtime escalation
"""

from __future__ import annotations

import logging
from typing import Callable

from app.agents.escape import classify as classify_escape
from app.agents.nodes import register_node_type

logger = logging.getLogger(__name__)


def build_interrupt_node_factory(data: dict) -> Callable:
    prompt_template = data.get("prompt_template") or data.get("prompt") or ""
    voice_prompt_template = data.get("voice_prompt_template") or prompt_template
    targets_slot = data.get("targets_slot")

    async def handler(state: dict) -> dict:
        from app.utils.templates import resolve_templates

        channel = state.get("channel", "chat")
        template = voice_prompt_template if channel == "voice" else prompt_template
        prompt = str(resolve_templates(template, state))

        logger.info(
            "[subagent_interrupt.v1] targets_slot=%s prompt_len=%d",
            targets_slot, len(prompt),
        )

        variables = dict(state.get("variables") or {})
        variables["_pending_interrupt_payload"] = {
            "kind": "slot_prompt",
            "prompt": prompt,
            "channel": channel,
            "targets_slot": targets_slot,
        }
        return {
            "variables": variables,
            "last_prompted_slot": targets_slot,
        }

    return handler


def apply_resume_escape(state: dict, user_text: str) -> dict:
    """Called by the driver after reading a resumed user utterance. Runs the
    escape classifier and sets `_escape_kind` + `_escape_hint` on state if the
    utterance is an abort/topic change. Kept separate from the handler so
    the classifier only sees a real user reply, not templated prompt content.
    """
    esc = classify_escape(user_text or "")
    if esc.kind in ("abort", "topic_change"):
        variables = dict(state.get("variables") or {})
        variables["_escape_kind"] = esc.kind
        variables["_escape_hint"] = user_text if esc.kind == "topic_change" else None
        variables["_escape_intent"] = esc.candidate_intent
        return {"variables": variables}
    return {}


register_node_type("interrupt_node", build_interrupt_node_factory)

"""Planner-filled sub-agent parameters — shared helpers.

A sub-agent template may declare `parameters` (agent-level; see
SubAgentTemplate.parameters). These merge into the entry tool's OpenAI
function schema so the orchestrator (Planner) LLM fills them in the same
call where it picks the agent. Valid values seed the inner graph's
`variables`, letting parse_node skip or narrow its extraction LLM call.

Three invariants live here:

1. **Lenient validation ("drop, don't die")** — the Planner may hallucinate.
   Invalid values are dropped with a log line, never an error; the flow
   degrades to the parse/interrupt path.
2. **Shared emptiness rule** — `is_filled()` matches the predicate DSL's
   `has()` (predicates.py): None, "", [], {} are all "unfilled". The
   parse-skip check and the seeding filter must agree with `has()` or a
   dispatcher can prompt for a slot the parser no longer extracts.
3. **Seed-once per tool_call** — the driver loop in the entry tools re-runs
   `execute()` from the top on every LangGraph interrupt replay. Seeding is
   keyed to the orchestrator's tool_call id so replays never re-apply
   (potentially stale) Planner args, and a genuinely new Planner call over
   an abandoned flow fills only still-empty slots.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Bookkeeping keys kept in inner-graph `variables`. Underscore-prefixed:
# template validation rejects parameter writes to `_`-names, and the
# predicate DSL treats them as ordinary (never-templated) slots.
SEEDED_CALL_ID_VAR = "_planner_args_call_id"
SEEDED_FLAG_VAR = "_planner_seeded"

_JSON_TYPES = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


def template_parameters(agent_name: str) -> dict:
    """Declared parameters for an agent, read from its template. Parameters
    are agent-level (store-synced across channel variants) so any variant
    works — `template_for_agent` falls back to chat. Used by the hand-coded
    regulated entry tools; DynamicSubAgentTool instead receives parameters
    at registration time."""
    from app.agents import template_for_agent

    template = template_for_agent(agent_name, "chat")
    if template is None:
        return {}
    return dict(getattr(template, "parameters", None) or {})


def is_filled(value) -> bool:
    """Emptiness rule shared with predicates.has(): None/""/[]/{} are unfilled.
    False and 0 ARE filled (a parsed `confirmed=False` is an answer)."""
    return value is not None and value != "" and value != [] and value != {}


def merge_input_schema(base_message_description: str, parameters: dict) -> dict:
    """The entry tool's OpenAI parameters schema: the `message` field plus
    declared properties. With empty `parameters` this returns exactly the
    legacy one-field schema (backward compatibility is asserted in tests)."""
    properties = {
        "message": {
            "type": "string",
            "description": base_message_description,
        },
    }
    required = ["message"]
    props = (parameters or {}).get("properties") or {}
    for name, spec in props.items():
        entry = {"type": spec.get("type", "string")}
        if spec.get("description"):
            entry["description"] = spec["description"]
        if spec.get("enum"):
            entry["enum"] = list(spec["enum"])
        properties[name] = entry
    required += [r for r in (parameters or {}).get("required") or [] if r in props]
    return {"type": "object", "properties": properties, "required": required}


def filter_valid_args(args: dict, parameters: dict, *, agent_name: str) -> dict:
    """Return only declared args whose values pass type/enum checks.

    Drops (with a log line, never an error): undeclared keys, None, empty
    values per is_filled(), JSON-type mismatches (bool checked before the
    numeric types — isinstance(True, int) is True in Python), and enum
    violations."""
    valid: dict = {}
    props = (parameters or {}).get("properties") or {}
    for name, value in (args or {}).items():
        if name == "message":
            continue
        spec = props.get(name)
        if spec is None:
            _drop(agent_name, name, "undeclared")
            continue
        if not is_filled(value):
            _drop(agent_name, name, "empty")
            continue
        ptype = spec.get("type", "string")
        if isinstance(value, bool) and ptype != "boolean":
            _drop(agent_name, name, f"type:{ptype}")
            continue
        expected = _JSON_TYPES.get(ptype, str)
        if not isinstance(value, expected):
            _drop(agent_name, name, f"type:{ptype}")
            continue
        enum = spec.get("enum")
        if enum and value not in enum:
            _drop(agent_name, name, "enum")
            continue
        valid[name] = value
    return valid


def _drop(agent_name: str, param: str, reason: str) -> None:
    logger.info(
        "[sub_agent_arg_dropped.v1] agent=%s param=%s reason=%s",
        agent_name, param, reason,
    )


def seed_variables(
    variables: dict,
    valid_args: dict,
    parameters: dict,
    *,
    fill_empty_only: bool = False,
) -> dict:
    """Apply validated Planner args to a variables dict via the declared
    writes map (identity default). fill_empty_only protects slots already
    gathered interactively (continuation over an abandoned flow)."""
    from app.agents.template_loader import effective_parameter_writes

    out = dict(variables)
    writes = effective_parameter_writes(parameters or {})
    for name, value in valid_args.items():
        var = writes.get(name)
        if not var:
            continue
        if fill_empty_only and is_filled(out.get(var)):
            continue
        out[var] = value
    return out


def apply_planner_args(
    state: dict,
    valid_args: dict,
    parameters: dict,
    *,
    tool_call_id: str,
) -> dict:
    """Seed-once guard around seed_variables (invariant 3 above).

    - Same tool_call_id as the recorded seed → replay: no re-seed, no flag.
    - Different (or first) tool_call_id → seed. fill_empty_only when the
      state already carries interactively-gathered variables (continuation).
    - Sets SEEDED_FLAG_VAR so parse_node applies its skip/narrow logic on
      exactly this pass; parse_node clears the flag.

    Mutates and returns `state`.
    """
    variables = dict(state.get("variables") or {})
    already_for_call = variables.get(SEEDED_CALL_ID_VAR)
    if tool_call_id and already_for_call == tool_call_id:
        state["variables"] = variables
        return state  # replay of the same Planner call — never re-seed

    is_continuation = any(
        not k.startswith("_") and is_filled(v) for k, v in variables.items()
    )
    variables = seed_variables(
        variables, valid_args, parameters, fill_empty_only=is_continuation
    )
    variables[SEEDED_CALL_ID_VAR] = tool_call_id or ""
    if valid_args or (parameters or {}).get("properties"):
        # Gate parse skip/narrow to this Planner-entry pass. Set even when
        # zero args validated — parse then sees "nothing filled" and runs
        # in full, but the resume passes never inherit skip behavior.
        variables[SEEDED_FLAG_VAR] = True
    state["variables"] = variables
    return state

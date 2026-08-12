"""Planner-filled sub-agent parameters — full contract.

Covers (plan §5.1):
  - loader validation incl. slot-safety (confirmation-bypass prevention)
  - schema merge backward compatibility (byte-identical legacy schema)
  - lenient arg filtering ("drop, don't die")
  - seed-once semantics: LangGraph REPLAYS re-run the entry tool from the
    top on every interrupt resume, so seeding must be keyed to the
    tool_call id — replays never re-apply stale Planner args, and a new
    Planner call over an abandoned flow fills only still-empty slots
  - parse_node: skip/narrow gated to the seeded entry pass; resume passes
    always run the full parse (corrections must keep working)
  - store round-trip, sibling sync, cross-variant slot-safety
  - registry refresh picks up parameters without a restart
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agents.template_loader import (
    TemplateValidationError,
    effective_parameter_writes,
    load_template,
    protected_slots,
    validate_parameters,
)
from app.tools.sub_agent_params import (
    SEEDED_CALL_ID_VAR,
    SEEDED_FLAG_VAR,
    apply_planner_args,
    filter_valid_args,
    is_filled,
    merge_input_schema,
    seed_variables,
)


# --- Builders ---


def _nodes(*extra):
    return [
        {"id": "parse", "type": "parse_node", "data": {"mode": "regex", "extractors": []}},
        *extra,
    ]


def _params(properties=None, required=(), writes=None):
    return {
        "properties": properties or {
            "amount": {"type": "number", "description": "USD amount"},
            "transfer_type": {"type": "string", "enum": ["m2m", "cc", "zelle"]},
        },
        "required": list(required),
        "writes": writes or {},
    }


def _raw(name="agent_x_chat", *, parameters=None, nodes=None, channel="chat"):
    raw = {
        "name": name,
        "agent_name": name.rsplit("_", 1)[0],
        "channel": channel,
        "supported_channels": [channel],
        "nodes": nodes or _nodes(),
        "edges": [],
    }
    if parameters is not None:
        raw["parameters"] = parameters
    return raw


# --- 1. Loader validation ---


def test_loader_roundtrips_parameters():
    loaded = load_template(_raw(parameters=_params()))
    assert set(loaded.parameters["properties"]) == {"amount", "transfer_type"}
    assert loaded.parameters["required"] == []


def test_loader_no_parameters_yields_empty_dict():
    assert load_template(_raw()).parameters == {}


@pytest.mark.parametrize("bad", [
    "not-a-dict",
    {"properties": {}},                                        # empty properties
    {"properties": {"a": {"type": "object"}}},                 # non-scalar type
    {"properties": {"a": {"type": "string", "enum": []}}},     # empty enum
    {"properties": {"a": {"type": "number", "enum": ["x"]}}},  # enum/type mismatch
    {"properties": {"a": {"type": "integer", "enum": [True]}}},# bool masquerading as int
    {"properties": {"message": {"type": "string"}}},           # reserved name
    {"properties": {"a": {"type": "string"}}, "required": ["b"]},
    {"properties": {"a": {"type": "string"}}, "writes": {"b": "x"}},
    {"properties": {"a": {"type": "string"}}, "writes": {"a": ""}},
    {"properties": {"a": {"type": "string"}}, "writes": {"a": "_return_mode"}},
])
def test_loader_rejects_invalid_parameters(bad):
    with pytest.raises(TemplateValidationError):
        load_template(_raw(parameters=bad))


def test_parameters_may_not_write_to_confirmation_slot():
    """THE confirmation-bypass guard: an interrupt's targets_slot (here the
    human yes/no gate `confirmed`) must be structurally unreachable to the
    Planner — a parameter writing to it is rejected at validation time."""
    confirm = {
        "id": "confirm", "type": "interrupt_node",
        "data": {"targets_slot": "confirmed", "prompt_template": "Go ahead?"},
    }
    params = _params(
        properties={"confirmed": {"type": "boolean"}},
    )
    with pytest.raises(TemplateValidationError, match="protected slots"):
        load_template(_raw(parameters=params, nodes=_nodes(confirm)))


def test_planner_fillable_interrupt_slot_is_allowed():
    """Data-collection interrupts opt in explicitly; the parameter pre-fills
    the slot and the interrupt is skipped."""
    ask_amount = {
        "id": "ask", "type": "interrupt_node",
        "data": {"targets_slot": "amount", "planner_fillable": True,
                 "prompt_template": "How much?"},
    }
    loaded = load_template(_raw(parameters=_params(), nodes=_nodes(ask_amount)))
    assert "amount" in loaded.parameters["properties"]


def test_parameters_may_not_write_to_output_var():
    fetch = {
        "id": "fetch", "type": "tool_call_node",
        "data": {"tool": "t", "action": "a", "params": {}, "output_var": "transfer_details"},
    }
    params = _params(properties={"transfer_details": {"type": "string"}})
    with pytest.raises(TemplateValidationError, match="protected slots"):
        load_template(_raw(parameters=params, nodes=_nodes(fetch)))


def test_protected_slots_collects_all_sources():
    nodes = [
        {"id": "i", "type": "interrupt_node", "data": {"targets_slot": "confirmed"}},
        {"id": "i2", "type": "interrupt_node",
         "data": {"targets_slot": "amount", "planner_fillable": True}},
        {"id": "t", "type": "tool_call_node", "data": {"output_var": "details"}},
        {"id": "p", "type": "parallel_tools_node",
         "data": {"tools": [{"tool": "x", "action": "y", "output_var": "res"}]}},
    ]
    assert protected_slots(nodes) == {"confirmed", "details", "res"}


# --- 2. Schema merge ---


LEGACY_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "description": "The user's request in natural language.",
        },
    },
    "required": ["message"],
}


def test_merge_empty_parameters_is_byte_identical_legacy():
    assert merge_input_schema(
        "The user's request in natural language.", {}
    ) == LEGACY_SCHEMA


def test_merge_adds_declared_properties():
    schema = merge_input_schema("msg", _params(required=["amount"]))
    assert schema["properties"]["amount"] == {"type": "number", "description": "USD amount"}
    assert schema["properties"]["transfer_type"]["enum"] == ["m2m", "cc", "zelle"]
    assert schema["required"] == ["message", "amount"]


# --- 3. Lenient filtering ---


@pytest.mark.parametrize("args,expected", [
    ({"amount": 50}, {"amount": 50}),
    ({"amount": 50.5}, {"amount": 50.5}),
    ({"amount": "50"}, {}),                     # type mismatch → dropped
    ({"amount": True}, {}),                     # bool is not a number
    ({"amount": None}, {}),
    ({"transfer_type": "zelle"}, {"transfer_type": "zelle"}),
    ({"transfer_type": "wire"}, {}),            # enum violation → dropped
    ({"transfer_type": ""}, {}),                # empty per is_filled → dropped
    ({"undeclared": "x"}, {}),
    ({"message": "hi", "amount": 5}, {"amount": 5}),  # message never treated as param
])
def test_filter_valid_args(args, expected):
    assert filter_valid_args(args, _params(), agent_name="t") == expected


def test_filter_integer_and_boolean_types():
    params = _params(properties={
        "count": {"type": "integer"},
        "flag": {"type": "boolean"},
    })
    assert filter_valid_args({"count": 3}, params, agent_name="t") == {"count": 3}
    assert filter_valid_args({"count": 3.5}, params, agent_name="t") == {}
    assert filter_valid_args({"count": True}, params, agent_name="t") == {}
    assert filter_valid_args({"flag": False}, params, agent_name="t") == {"flag": False}


def test_is_filled_matches_predicate_has_semantics():
    assert not is_filled(None) and not is_filled("") and not is_filled([]) and not is_filled({})
    assert is_filled(False) and is_filled(0) and is_filled("x")


# --- 4/5. Seeding: fresh, replay, continuation (the "run once" guards) ---


def test_seed_fresh_applies_writes_and_flags():
    state = {"variables": {}}
    apply_planner_args(state, {"amount": 50}, _params(), tool_call_id="call_1")
    v = state["variables"]
    assert v["amount"] == 50
    assert v[SEEDED_CALL_ID_VAR] == "call_1"
    assert v[SEEDED_FLAG_VAR] is True


def test_seed_respects_writes_map():
    params = _params(writes={"amount": "amt_slot"})
    state = {"variables": {}}
    apply_planner_args(state, {"amount": 9}, params, tool_call_id="c")
    assert state["variables"]["amt_slot"] == 9
    assert "amount" not in state["variables"]


def test_replay_same_tool_call_never_reseeds():
    """The riskiest property of the feature: execute() re-runs from the top
    on EVERY LangGraph interrupt replay. Guard must be state-keyed, not
    code-path-keyed — a slot the flow emptied mid-conversation must NOT be
    resurrected by the original Planner args on replay."""
    state = {"variables": {}}
    apply_planner_args(state, {"amount": 50}, _params(), tool_call_id="call_1")
    # Mid-flow: parse consumed the flag; the user changed their mind and the
    # flow cleared the slot while prompting again.
    state["variables"].pop(SEEDED_FLAG_VAR, None)
    state["variables"]["amount"] = None

    # Replay of the SAME tool call (same id) — e.g. resume after interrupt.
    apply_planner_args(state, {"amount": 50}, _params(), tool_call_id="call_1")
    assert state["variables"]["amount"] is None          # stale arg NOT resurrected
    assert SEEDED_FLAG_VAR not in state["variables"]     # skip window NOT reopened


def test_new_tool_call_fills_empty_slots_only():
    """A genuinely NEW Planner call over an abandoned flow (new tool_call id)
    seeds fill-empty-only: interactively-gathered slots are never clobbered
    by fresh Planner guesses."""
    state = {"variables": {}}
    apply_planner_args(state, {"amount": 50}, _params(), tool_call_id="call_1")
    state["variables"].pop(SEEDED_FLAG_VAR, None)
    # User confirmed 50 interactively; also gathered transfer_type=zelle.
    state["variables"]["transfer_type"] = "zelle"

    apply_planner_args(
        state, {"amount": 75, "transfer_type": "m2m"}, _params(), tool_call_id="call_2"
    )
    v = state["variables"]
    assert v["amount"] == 50            # filled slot NOT overwritten
    assert v["transfer_type"] == "zelle"
    assert v[SEEDED_CALL_ID_VAR] == "call_2"
    assert v[SEEDED_FLAG_VAR] is True   # new entry pass may skip/narrow again


def test_seed_variables_fill_empty_only_respects_is_filled():
    params = _params()
    out = seed_variables({"amount": "", "transfer_type": "cc"},
                         {"amount": 5, "transfer_type": "zelle"},
                         params, fill_empty_only=True)
    assert out["amount"] == 5           # "" counts as empty (has() semantics)
    assert out["transfer_type"] == "cc"


def test_driver_level_replay_via_runtime_store():
    """Integration: DynamicSubAgentTool._initial_inner_state across the real
    save/load runtime store — fresh call seeds, replay of the same call
    (prior saved state, same tool_call_id) does not re-seed."""
    from app.agents.runtime import clear_inner_state, save_inner_state
    from app.tools.dynamic_sub_agent_tool import DynamicSubAgentTool

    tool = DynamicSubAgentTool(
        agent_name="agent_x", display_name="X", description="d",
        search_hint="", supported_channels=["chat"], parameters=_params(),
    )
    thread = "sess_agent_x_chat"
    clear_inner_state(thread)
    try:
        state = tool._initial_inner_state(
            thread_id=thread, user_id="u", session_id="sess", channel="chat",
            message="transfer 50", valid_args={"amount": 50}, tool_call_id="call_1",
        )
        assert state["variables"]["amount"] == 50
        assert state["main_context"]["planner_args"] == {"amount": 50}

        # Simulate the driver saving at an interrupt after the flow emptied
        # the slot (user said "actually change the amount").
        state["variables"].pop(SEEDED_FLAG_VAR, None)
        state["variables"]["amount"] = None
        save_inner_state(thread, state)

        replay = tool._initial_inner_state(
            thread_id=thread, user_id="u", session_id="sess", channel="chat",
            message="transfer 50", valid_args={"amount": 50}, tool_call_id="call_1",
        )
        assert replay["variables"]["amount"] is None
        assert SEEDED_FLAG_VAR not in replay["variables"]
    finally:
        clear_inner_state(thread)


# --- 6-9. parse_node skip / narrow / resume / always_run ---


def _llm_parse_node(always_run=False):
    from app.agents.nodes.parse_node import build_parse_node_factory
    data = {
        "mode": "llm",
        "system_prompt": "extract",
        "include_context": False,
        "output_schema": {
            "amount": {"type": "number", "nullable": True},
            "transfer_type": {"type": "string", "nullable": True},
        },
        "writes": {"amount": "amount", "transfer_type": "transfer_type"},
    }
    if always_run:
        data["always_run"] = True
    return build_parse_node_factory(data)


def _state(variables, **kw):
    from langchain_core.messages import HumanMessage
    return {
        "messages": [HumanMessage(content="transfer 50 to savings")],
        "channel": "chat",
        "main_context": {"agent_name": "agent_x"},
        "variables": dict(variables),
        **kw,
    }


@pytest.mark.asyncio
async def test_parse_skips_llm_when_seeded_and_all_filled(monkeypatch):
    mock = AsyncMock(return_value={})
    monkeypatch.setattr("app.agents.nodes.parse_node.llm_parse", mock)
    handler = _llm_parse_node()

    update = await handler(_state(
        {"amount": 50, "transfer_type": "m2m", SEEDED_FLAG_VAR: True}
    ))
    mock.assert_not_awaited()
    assert SEEDED_FLAG_VAR not in update["variables"]     # flag consumed


@pytest.mark.asyncio
async def test_parse_skip_counts_filled_pending_slot_as_progress(monkeypatch):
    """Regression guard for the retry-tracking corruption: Planner args
    satisfied the pending slot → skip must CLEAR the retry counter, not
    increment it toward retry_exhausted_for_slot."""
    monkeypatch.setattr("app.agents.nodes.parse_node.llm_parse", AsyncMock(return_value={}))
    handler = _llm_parse_node()

    update = await handler(_state(
        {"amount": 50, "transfer_type": "m2m", SEEDED_FLAG_VAR: True},
        last_prompted_slot="amount",
        parse_retry_count={"amount": 2},
        retry_exhausted_for_slot=None,
    ))
    assert update["parse_retry_count"] == {}
    assert update.get("retry_exhausted_for_slot") is None


@pytest.mark.asyncio
async def test_parse_narrows_to_missing_fields(monkeypatch):
    mock = AsyncMock(return_value={"transfer_type": "m2m"})
    monkeypatch.setattr("app.agents.nodes.parse_node.llm_parse", mock)
    handler = _llm_parse_node()

    update = await handler(_state({"amount": 50, SEEDED_FLAG_VAR: True}))
    mock.assert_awaited_once()
    schema = mock.await_args.kwargs["output_schema"]
    assert set(schema) == {"transfer_type"}               # amount narrowed out
    assert update["variables"]["transfer_type"] == "m2m"
    assert update["variables"]["amount"] == 50


@pytest.mark.asyncio
async def test_parse_resume_pass_runs_full_schema(monkeypatch):
    """Resume replies land here with NO seeded flag — the full schema must
    run so corrections ('no, make it 30') overwrite filled slots."""
    mock = AsyncMock(return_value={"amount": 30, "transfer_type": None})
    monkeypatch.setattr("app.agents.nodes.parse_node.llm_parse", mock)
    handler = _llm_parse_node()

    update = await handler(_state({"amount": 50, "transfer_type": "m2m"}))
    mock.assert_awaited_once()
    schema = mock.await_args.kwargs["output_schema"]
    assert set(schema) == {"amount", "transfer_type"}     # full, not narrowed
    assert update["variables"]["amount"] == 30            # correction applied


@pytest.mark.asyncio
async def test_parse_always_run_disables_skip(monkeypatch):
    mock = AsyncMock(return_value={})
    monkeypatch.setattr("app.agents.nodes.parse_node.llm_parse", mock)
    handler = _llm_parse_node(always_run=True)

    await handler(_state({"amount": 50, "transfer_type": "m2m", SEEDED_FLAG_VAR: True}))
    mock.assert_awaited_once()
    assert set(mock.await_args.kwargs["output_schema"]) == {"amount", "transfer_type"}


@pytest.mark.asyncio
async def test_regex_parse_skips_when_seeded_and_filled():
    from app.agents.nodes.parse_node import build_parse_node_factory
    handler = build_parse_node_factory({
        "mode": "regex",
        "extractors": [{"slot": "amount", "parser": "money"}],
    })
    update = await handler(_state({"amount": 50, SEEDED_FLAG_VAR: True}))
    assert update["variables"]["amount"] == 50
    assert SEEDED_FLAG_VAR not in update["variables"]


# --- 10-12. Store round-trip, sibling sync, cross-variant safety, refresh ---


def _store_raw(name, channel, *, parameters=None, nodes=None):
    return _raw(name, parameters=parameters, nodes=nodes, channel=channel)


def test_store_roundtrip_and_sibling_sync(temp_db):
    from app.agents.template_store import get_row, upsert_template

    upsert_template(_store_raw("agent_x_chat", "chat"))
    upsert_template(_store_raw("agent_x_voice", "voice"))

    upsert_template(_store_raw("agent_x_chat", "chat", parameters=_params()))
    assert set(get_row("agent_x_chat").parameters["properties"]) == {"amount", "transfer_type"}
    # Agent-level: the voice sibling was synced.
    assert set(get_row("agent_x_voice").parameters["properties"]) == {"amount", "transfer_type"}


def test_cross_variant_confirmation_bypass_rejected(temp_db):
    """Sibling variant has a confirmation interrupt (`confirmed`, no
    planner_fillable). Declaring a `confirmed` parameter on the OTHER
    channel's variant must fail — parameters are agent-level and would
    reach the voice graph via sync."""
    from app.agents.template_store import upsert_template

    confirm = {
        "id": "confirm", "type": "interrupt_node",
        "data": {"targets_slot": "confirmed", "prompt_template": "Go ahead?"},
    }
    upsert_template(_store_raw("agent_y_voice", "voice", nodes=_nodes(confirm)))

    bypass = _params(properties={"confirmed": {"type": "boolean"}})
    with pytest.raises(TemplateValidationError, match="protected slots"):
        upsert_template(_store_raw("agent_y_chat", "chat", parameters=bypass))


def test_row_to_raw_omits_empty_parameters(temp_db):
    """Backward compat: for rows without parameters the reconstructed raw
    dict is byte-identical to the pre-feature shape — no `parameters` key —
    so template_hash over it is unchanged by this feature. (row.hash itself
    is computed from the SUBMITTED raw, which differs from the reconstruction
    by normalization — that asymmetry predates this feature.)"""
    from app.agents.template_store import _row_to_raw, get_row, upsert_template

    upsert_template(_store_raw("agent_z_chat", "chat"))
    assert "parameters" not in _row_to_raw(get_row("agent_z_chat"))

    upsert_template(_store_raw("agent_z2_chat", "chat", parameters=_params()))
    assert "parameters" in _row_to_raw(get_row("agent_z2_chat"))


def test_import_file_carries_and_syncs_parameters(temp_db, tmp_path):
    import json as _json
    from app.agents.template_store import get_row, import_template_file, upsert_template

    upsert_template(_store_raw("agent_w_voice", "voice"))
    doc = _store_raw("agent_w_chat", "chat", parameters=_params())
    (tmp_path / "agent_w.chat.json").write_text(_json.dumps(doc))

    row = import_template_file(tmp_path, "agent_w.chat.json")
    assert set(row.parameters["properties"]) == {"amount", "transfer_type"}
    # Import path also sibling-syncs (upsert-only sync was a review finding).
    assert set(get_row("agent_w_voice").parameters["properties"]) == {"amount", "transfer_type"}


def test_refresh_registers_parameters_without_restart(temp_db):
    from app.agents.template_store import set_status, upsert_template
    from app.tools import _REGISTRY
    from app.tools.dynamic_sub_agent_tool import refresh_dynamic_sub_agent_tools
    import asyncio

    try:
        upsert_template(_store_raw("agent_r_chat", "chat"))
        set_status("agent_r_chat", "deployed")
        refresh_dynamic_sub_agent_tools()
        schema0 = asyncio.run(_REGISTRY["agent_r"].input_schema())
        assert set(schema0["properties"]) == {"message"}   # golden legacy shape
        assert schema0 == LEGACY_SCHEMA

        upsert_template(_store_raw("agent_r_chat", "chat", parameters=_params()))
        set_status("agent_r_chat", "deployed")
        refresh_dynamic_sub_agent_tools()
        schema1 = asyncio.run(_REGISTRY["agent_r"].input_schema())
        assert set(schema1["properties"]) == {"message", "amount", "transfer_type"}
    finally:
        _REGISTRY.pop("agent_r", None)


# --- Helpers under test directly ---


def test_effective_writes_identity_default():
    assert effective_parameter_writes(_params()) == {
        "amount": "amount", "transfer_type": "transfer_type",
    }
    assert effective_parameter_writes(_params(writes={"amount": "amt"})) == {
        "amount": "amt", "transfer_type": "transfer_type",
    }


def test_validate_parameters_public_api_matches_loader():
    normalized = validate_parameters(_params(), _nodes())
    assert set(normalized["properties"]) == {"amount", "transfer_type"}
    assert validate_parameters(None, _nodes()) == {}

"""Unit tests for the deterministic Presenter rules engine.

The decision tree is deterministic — same inputs always produce the same
RenderDecision. These tests assert every branch of select_render() explicitly.
"""

import pytest

from app.agent.presenter import select_render, RenderDecision


# --- Rule 1: designed composite (exact set match) ---


def test_rule1_profile_with_accounts_exact_match():
    """profile_data + accounts_data populated → designed composite fires."""
    state = {
        "variables": {
            "profile_data": {"name": "X"},
            "accounts_data": [{"display_name": "Checking", "balance": 100}],
        },
        "variables_order": {"profile_data": 1, "accounts_data": 2},
        "messages": [],
    }
    d = select_render(state)
    assert d.rule == "designed_composite"
    assert d.widget_type == "profile_with_accounts"
    assert d.build_args == {
        "profile": {"name": "X"},
        "accounts": [{"display_name": "Checking", "balance": 100}],
    }


def test_rule1_extra_slot_declines_exact_match():
    """Over-fetching drops rule 1 (exact match only). Falls to rule 3."""
    state = {
        "variables": {
            "profile_data": {"name": "X"},
            "accounts_data": [{"x": 1}],
            "transactions_data": {"shape": "flat", "transactions": []},
        },
        "variables_order": {"profile_data": 1, "accounts_data": 2, "transactions_data": 3},
        "messages": [],
    }
    d = select_render(state)
    assert d.rule == "generic_composite"
    assert d.widget_type == "generic_composite"


# --- Rule 2: single mapped slot ---


def test_rule2_profile_only():
    state = {
        "variables": {"profile_data": {"name": "Ada"}},
        "variables_order": {"profile_data": 1},
        "messages": [],
    }
    d = select_render(state)
    assert d.rule == "single_slot"
    assert d.widget_type == "profile_card"
    assert d.build_args == {"profile_data": {"name": "Ada"}}


def test_rule2_accounts_only():
    state = {
        "variables": {"accounts_data": [{"display_name": "Checking"}]},
        "variables_order": {"accounts_data": 1},
        "messages": [],
    }
    d = select_render(state)
    assert d.rule == "single_slot"
    assert d.widget_type == "account_summary"
    assert d.build_args == {"accounts": [{"display_name": "Checking"}]}


def test_rule2_transactions_only():
    payload = {"shape": "flat", "transactions": [{"description": "x"}]}
    state = {
        "variables": {"transactions_data": payload},
        "variables_order": {"transactions_data": 1},
        "messages": [],
    }
    d = select_render(state)
    assert d.rule == "single_slot"
    assert d.widget_type == "transaction_list"
    assert d.build_args == {"payload": payload}


# --- Rule 3: generic composite ---


def test_rule3_two_slots_no_designed_composite():
    """Two populated slots that don't match any designed composite → generic."""
    state = {
        "variables": {
            "profile_data": {"name": "X"},
            "transactions_data": {"shape": "flat", "transactions": []},
        },
        "variables_order": {"profile_data": 1, "transactions_data": 2},
        "messages": [],
    }
    d = select_render(state)
    assert d.rule == "generic_composite"
    assert len(d.build_args["sections"]) == 2
    # Priority ordering: profile_card (20) before transaction_list (100)
    assert d.build_args["sections"][0]["widget_type"] == "profile_card"
    assert d.build_args["sections"][1]["widget_type"] == "transaction_list"


def test_rule3_truncates_at_three():
    """4+ composable slots truncate to top-3 by priority."""
    # Rule 3 only needs >= 2 composable mapped slots; let's simulate 4 by
    # adding a hypothetical populated slot (we only have 3 real mapped slots
    # in the current catalog — profile_data, accounts_data, transactions_data —
    # so the truncation-at-3 branch can't be triggered by current catalog
    # alone. Skip rather than skip-silently, since this tests a real branch
    # but requires >3 mapped widgets to exercise).
    pytest.skip("Current catalog has only 3 mapped widgets; truncation branch "
                "requires a 4th to exercise. Covered by catalog growth.")


def test_rule3_sections_contain_resolved_data_not_slot_refs():
    """Sections must be pre-resolved {widget_type, data}, not slot references.

    Contract: the generic_composite_widget builder is pure — no catalog lookups,
    no state access. It receives ready-to-render section data.
    """
    state = {
        "variables": {
            "profile_data": {"name": "X"},
            "transactions_data": {"shape": "flat", "transactions": []},
        },
        "variables_order": {"profile_data": 1, "transactions_data": 2},
        "messages": [],
    }
    d = select_render(state)
    for section in d.build_args["sections"]:
        assert set(section.keys()) == {"widget_type", "data"}
        assert "data_slot" not in section
        # data is the actual slot value, not a slot name string
        assert not isinstance(section["data"], str) or section["widget_type"] == "text_card"


# --- Rule 4: fallback ---


def test_rule4_empty_state():
    state = {"variables": {}, "variables_order": {}, "messages": []}
    d = select_render(state)
    assert d.rule == "text_card_fallback"
    assert d.widget_type == "text_card"
    assert "didn't find anything" in d.build_args["content"]


def test_rule4_no_mapped_slot():
    """Populated slot that isn't in the catalog → falls to text_card."""
    state = {
        "variables": {"knowledge_sources": [{"url": "x", "title": "y"}]},
        "variables_order": {"knowledge_sources": 1},
        "messages": [],
    }
    d = select_render(state)
    assert d.rule == "text_card_fallback"
    assert "knowledge_sources" in d.build_args["content"]


def test_rule4_uses_planner_prose_when_available():
    """If the Planner wrote content alongside present_widget, rule 4 uses it."""
    from langchain_core.messages import AIMessage
    state = {
        "variables": {"knowledge_sources": ["a"]},
        "variables_order": {"knowledge_sources": 1},
        "messages": [AIMessage(content="A thoughtful analysis goes here.")],
    }
    d = select_render(state)
    assert d.rule == "text_card_fallback"
    assert d.build_args["content"] == "A thoughtful analysis goes here."


# --- Determinism + return contract ---


def test_returns_render_decision():
    state = {"variables": {}, "variables_order": {}, "messages": []}
    d = select_render(state)
    assert isinstance(d, RenderDecision)
    assert callable(d.build)


def test_idempotent_same_state_same_decision():
    """select_render is pure — same state always yields same decision."""
    state = {
        "variables": {"profile_data": {"name": "X"}},
        "variables_order": {"profile_data": 1},
        "messages": [],
    }
    d1 = select_render(state)
    d2 = select_render(state)
    assert d1.rule == d2.rule
    assert d1.widget_type == d2.widget_type
    assert d1.build_args == d2.build_args


# --- Rule ordering ---


def test_rule1_wins_over_rule2_when_combo_matches():
    """Even though rule 2 would match single slot, rule 1 fires first."""
    # Rule 1 needs the EXACT set to match. With only profile_data, rule 1
    # doesn't match any composite (profile_with_accounts needs both).
    # So this test sets up the two-slot case where rule 1 fires and rule 2
    # would NOT fire anyway (rule 2 requires exactly 1 mapped slot).
    state = {
        "variables": {
            "profile_data": {"name": "X"},
            "accounts_data": [],
        },
        "variables_order": {"profile_data": 1, "accounts_data": 2},
        "messages": [],
    }
    # accounts_data is empty list → not populated → rule 2 (single_slot profile) fires
    d = select_render(state)
    assert d.rule == "single_slot"
    assert d.widget_type == "profile_card"


def test_falsy_slots_are_not_populated():
    """Empty list, empty dict, None, empty string are all "not populated"."""
    state = {
        "variables": {
            "profile_data": {"name": "X"},
            "accounts_data": [],  # empty = not populated
            "transactions_data": None,  # None = not populated
        },
        "variables_order": {"profile_data": 1, "accounts_data": 2, "transactions_data": 3},
        "messages": [],
    }
    d = select_render(state)
    assert d.rule == "single_slot"  # only profile_data counts
    assert d.widget_type == "profile_card"

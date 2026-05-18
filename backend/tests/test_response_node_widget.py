"""§4.7 — response_node widget dispatch tests.

Covers the dual-path runtime (§4.3) and fallback-on-missing-required (§4.3.1).
See backend/docs/widget_response_node_migration.md.
"""

import asyncio

import pytest

from app.agents.nodes.response_node import build_response_node_factory


def _state(**overrides):
    base = {
        "variables": {
            "amount": 200,
            "from_account": {"displayName": "Checking"},
            "to_account": {"displayName": "Savings"},
            "source_options": [{"displayName": "Checking"}],
            "target_options": [{"displayName": "Savings"}],
        },
        "messages": [],
    }
    base["variables"].update(overrides.pop("variables", {}))
    base.update(overrides)
    return base


def _run(data, state):
    handler = build_response_node_factory(data)
    return asyncio.run(handler(state))


def _widget_node(**widget_overrides):
    cfg = {
        "widget_type": "transfer_form",
        "kwargs": {
            "amount": "{{variables.amount}}",
            "from_account": "{{variables.from_account}}",
            "to_account": "{{variables.to_account}}",
            "source_options": "{{variables.source_options}}",
            "target_options": "{{variables.target_options}}",
            "transfer_type": "m2m",
            "title": "Confirm transfer",
        },
    }
    cfg.update(widget_overrides)
    return {"return_mode": "widget", "widget": cfg}


# --- New path (builder) ---


def test_new_path_calls_builder_and_sets_terminal():
    r = _run(_widget_node(), _state())
    assert r["_terminal"] is True
    assert r["variables"]["_return_mode"] == "widget"
    widget = r["variables"]["_response_widget"]
    assert widget["widget"] == "transfer_form"
    assert widget["title"] == "Confirm transfer"
    # New transfer_form_widget kwargs reach `widget.data`:
    assert widget["data"]["transfer_type"] == "m2m"
    # Builder owns actions; template can't supply them.
    assert widget["actions"] == [
        {"id": "submit", "label": "Transfer", "style": "primary"},
        {"id": "cancel", "label": "Cancel", "style": "secondary"},
    ]


def test_new_path_passes_through_resolved_types():
    """Single-template substitution returns raw value with native type."""
    r = _run(_widget_node(), _state(variables={"amount": 350}))
    assert r["variables"]["_response_widget"]["data"]["amount"] == 350


def test_new_path_metadata_merges_onto_builder_default():
    node = _widget_node(metadata={"flow": "chat", "transfer_type": "m2m"})
    r = _run(node, _state())
    md = r["variables"]["_response_widget"]["metadata"]
    assert md["status"] == "pending"  # from builder
    assert md["flow"] == "chat"  # from template
    assert md["transfer_type"] == "m2m"


def test_new_path_template_actions_are_ignored(caplog):
    node = _widget_node(actions=[{"id": "override", "label": "X"}])
    with caplog.at_level("WARNING"):
        r = _run(node, _state())
    assert any("widget_actions_ignored" in rec.message for rec in caplog.records)
    # Builder actions still in effect:
    assert {a["id"] for a in r["variables"]["_response_widget"]["actions"]} == {"submit", "cancel"}


# --- Required-kwarg + fallback (§4.3.1) ---


def test_missing_required_raises_when_mode_is_error():
    node = _widget_node()
    with pytest.raises(ValueError, match="required kwargs missing"):
        _run(node, _state(variables={"source_options": []}))


def test_missing_required_falls_back_to_text_when_mode_is_fallback():
    node = _widget_node(
        on_missing_required="fallback_text",
        fallback_text="I could not put your transfer together — please retry.",
    )
    r = _run(node, _state(variables={"source_options": []}))
    assert r["variables"]["_return_mode"] == "to_orchestrator"
    assert "could not put your transfer" in r["variables"]["_response_text"]
    assert "_response_widget" not in r["variables"]


def test_fallback_text_resolves_templates():
    node = _widget_node(
        on_missing_required="fallback_text",
        fallback_text="No options for {{variables.amount}} dollars.",
    )
    r = _run(node, _state(variables={"source_options": []}))
    assert r["variables"]["_response_text"] == "No options for 200 dollars."


def test_fallback_mode_with_empty_fallback_text_raises():
    node = _widget_node(on_missing_required="fallback_text", fallback_text="")
    with pytest.raises(ValueError, match="fallback_text is empty"):
        _run(node, _state(variables={"source_options": []}))


def test_whitespace_only_counts_as_missing():
    # source_options is required; an empty string still counts as missing.
    node = _widget_node()
    with pytest.raises(ValueError, match="required kwargs missing"):
        _run(node, _state(variables={"source_options": ""}))


# --- Legacy path ---


def test_legacy_path_used_when_only_data_template_present(caplog):
    node = {
        "return_mode": "widget",
        "widget": {
            "widget_type": "transfer_form",
            "title": "Legacy",
            "data_template": {
                "amount": "{{variables.amount}}",
                "from_account_hint": "{{variables.from_account_hint}}",
            },
        },
    }
    with caplog.at_level("WARNING"):
        r = _run(node, _state(variables={"from_account_hint": "checking"}))
    assert any("widget_legacy" in rec.message for rec in caplog.records)
    widget = r["variables"]["_response_widget"]
    assert widget["title"] == "Legacy"
    assert widget["data"]["amount"] == 200
    assert widget["data"]["from_account_hint"] == "checking"


def test_legacy_path_required_kwarg_check_does_not_fire():
    """Legacy templates have no catalog-required semantics."""
    node = {
        "return_mode": "widget",
        "widget": {
            "widget_type": "transfer_form",
            "data_template": {"amount": 100},  # source_options missing — legacy doesn't care
        },
    }
    r = _run(node, _state())
    assert r["variables"]["_return_mode"] == "widget"


def test_unknown_widget_type_raises():
    node = {
        "return_mode": "widget",
        "widget": {"widget_type": "definitely_not_a_widget", "kwargs": {}},
    }
    with pytest.raises(ValueError, match="unknown widget_type"):
        _run(node, _state())


def test_invalid_on_missing_required_rejected_at_factory_time():
    with pytest.raises(ValueError, match="on_missing_required"):
        build_response_node_factory({
            "return_mode": "widget",
            "widget": {
                "widget_type": "transfer_form",
                "kwargs": {},
                "on_missing_required": "spawn_an_llm",
            },
        })


# --- Feature flag ---


def test_feature_flag_off_forces_legacy_path(monkeypatch, caplog):
    from app.config import settings
    monkeypatch.setattr(settings, "feature_response_node_builder", False)
    node = {
        "return_mode": "widget",
        "widget": {
            "widget_type": "transfer_form",
            # Author provided kwargs, but flag is off → legacy fallback path.
            "kwargs": {"amount": "{{variables.amount}}", "source_options": "{{variables.source_options}}"},
            "data_template": {"amount": "{{variables.amount}}"},
        },
    }
    with caplog.at_level("WARNING"):
        r = _run(node, _state())
    assert r["variables"]["_return_mode"] == "widget"
    assert any("widget_legacy" in rec.message for rec in caplog.records)


def test_feature_flag_off_raises_when_no_data_template(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "feature_response_node_builder", False)
    node = _widget_node()  # only kwargs, no data_template
    with pytest.raises(ValueError, match="neither widget.kwargs nor widget.data_template"):
        _run(node, _state())

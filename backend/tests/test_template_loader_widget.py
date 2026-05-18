"""§4.6 — template loader validation for widget response_nodes.

Covers checks 1–6 (catalog lookup, kwarg names, required coverage, fallback
declaration, regulated guard, legacy warning).
"""

import pytest

from app.agents.template_loader import TemplateValidationError, load_template


def _base_template(**overrides):
    """Minimal valid template with a widget response_node."""
    tpl = {
        "name": "test_agent_chat",
        "agent_name": "test_agent",
        "channel": "chat",
        "nodes": [
            {
                "id": "r1",
                "type": "response_node",
                "data": {
                    "return_mode": "widget",
                    "widget": {
                        "widget_type": "transfer_form",
                        "kwargs": {
                            "source_options": "{{variables.s}}",
                            "target_options": "{{variables.t}}",
                            "title": "Confirm transfer",
                        },
                    },
                },
            }
        ],
        "edges": [],
        "entry_node": "r1",
    }
    if "node_data" in overrides:
        tpl["nodes"][0]["data"] = overrides.pop("node_data")
    tpl.update(overrides)
    return tpl


def test_check1_unknown_widget_type_rejected():
    tpl = _base_template()
    tpl["nodes"][0]["data"]["widget"]["widget_type"] = "no_such_widget"
    with pytest.raises(TemplateValidationError, match="unknown widget_type"):
        load_template(tpl)


def test_check2_unknown_kwarg_name_rejected():
    tpl = _base_template()
    tpl["nodes"][0]["data"]["widget"]["kwargs"]["bogus_field"] = "x"
    with pytest.raises(TemplateValidationError, match="unknown keys"):
        load_template(tpl)


def test_check2_title_is_allowlisted():
    """title sits at widget top level (not in `fields[]`) but must be accepted."""
    tpl = _base_template()
    # already has title — should load fine
    loaded = load_template(tpl)
    assert loaded.name == "test_agent_chat"


def test_check3_missing_required_kwarg_rejected_without_fallback():
    tpl = _base_template()
    # Drop target_options (required) without setting fallback_text mode.
    del tpl["nodes"][0]["data"]["widget"]["kwargs"]["target_options"]
    with pytest.raises(TemplateValidationError, match="required widget fields"):
        load_template(tpl)


def test_check3_missing_required_kwarg_ok_with_fallback_text():
    tpl = _base_template()
    del tpl["nodes"][0]["data"]["widget"]["kwargs"]["target_options"]
    tpl["nodes"][0]["data"]["widget"]["on_missing_required"] = "fallback_text"
    tpl["nodes"][0]["data"]["widget"]["fallback_text"] = "Could not assemble transfer."
    loaded = load_template(tpl)
    assert loaded.name == "test_agent_chat"


def test_check4_fallback_mode_without_fallback_text_rejected():
    tpl = _base_template()
    tpl["nodes"][0]["data"]["widget"]["on_missing_required"] = "fallback_text"
    # fallback_text omitted
    with pytest.raises(TemplateValidationError, match="fallback_text"):
        load_template(tpl)


def test_check4_invalid_on_missing_required_value_rejected():
    tpl = _base_template()
    tpl["nodes"][0]["data"]["widget"]["on_missing_required"] = "ask_an_llm"
    with pytest.raises(TemplateValidationError, match="on_missing_required"):
        load_template(tpl)


def test_check5_regulated_template_cannot_use_fallback_text():
    tpl = _base_template(is_regulated=True)
    tpl["nodes"][0]["data"]["widget"]["on_missing_required"] = "fallback_text"
    tpl["nodes"][0]["data"]["widget"]["fallback_text"] = "Something went wrong."
    with pytest.raises(TemplateValidationError, match="regulated template"):
        load_template(tpl)


def test_check5_regulated_template_with_error_mode_ok():
    tpl = _base_template(is_regulated=True)
    # default mode is "error" — required-kwargs are all present
    loaded = load_template(tpl)
    assert loaded.is_regulated is True


def test_check6_legacy_data_template_warns_not_fails():
    tpl = _base_template()
    tpl["nodes"][0]["data"]["widget"] = {
        "widget_type": "transfer_form",
        "title": "Legacy form",
        "data_template": {"amount": "{{variables.amount}}"},
    }
    loaded = load_template(tpl)
    assert any(
        "legacy widget.data_template" in w for w in loaded.warnings
    )


def test_existing_transfer_template_loads():
    """Production chat template must keep loading (legacy path warns only)."""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "app" / "agents" / "templates" / "transfer_money.chat.json"
    with open(path) as f:
        raw = json.load(f)
    loaded = load_template(raw)
    # Several legacy warnings expected, one per widget response_node.
    legacy = [w for w in loaded.warnings if "legacy widget.data_template" in w]
    assert len(legacy) >= 1


def test_existing_refund_template_loads():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "app" / "agents" / "templates" / "refund_fee.chat.json"
    with open(path) as f:
        raw = json.load(f)
    loaded = load_template(raw)
    legacy = [w for w in loaded.warnings if "legacy widget.data_template" in w]
    assert len(legacy) >= 1

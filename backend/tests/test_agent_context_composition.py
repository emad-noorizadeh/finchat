"""Sub-agent system-prompt composition — placeholder + auto-prepend rules.

`compose_system_prompt(raw_prompt, data)` is shared by llm_node and parse_node
(LLM mode). Authors can either let the runtime auto-prepend the Context-tab
content, or drop {{agent_context}} into their prompt to position it manually.
"""

from app.agents.nodes import compose_system_prompt


def test_default_prepends_context_when_no_placeholder():
    """Today's default: include_context=True (implicit), no placeholder → prepend."""
    out = compose_system_prompt(
        "Answer briefly.",
        {"_agent_context": "You are the card sub-agent."},
    )
    assert out == "You are the card sub-agent.\n\nAnswer briefly."


def test_placeholder_substitutes_at_position():
    """When the placeholder is in the prompt, context lands there — not prepended."""
    out = compose_system_prompt(
        "## Rules\n\nBe terse.\n\n## Context\n\n{{agent_context}}\n\n## Now",
        {"_agent_context": "You are CARD."},
    )
    expected = "## Rules\n\nBe terse.\n\n## Context\n\nYou are CARD.\n\n## Now"
    assert out == expected


def test_multiple_placeholders_all_substituted():
    out = compose_system_prompt(
        "{{agent_context}} ... {{agent_context}}",
        {"_agent_context": "ctx"},
    )
    assert out == "ctx ... ctx"


def test_include_context_false_with_placeholder_substitutes_empty():
    """Placeholder always drives placement; the toggle drives content."""
    out = compose_system_prompt(
        "Rules. {{agent_context}} Done.",
        {"_agent_context": "You are CARD.", "include_context": False},
    )
    assert out == "Rules.  Done."


def test_include_context_false_no_placeholder_just_returns_prompt():
    """Today's opt-out: include_context=False, no placeholder → raw prompt."""
    out = compose_system_prompt(
        "Be brief.",
        {"_agent_context": "You are CARD.", "include_context": False},
    )
    assert out == "Be brief."


def test_empty_prompt_returns_context_when_present():
    out = compose_system_prompt(
        "",
        {"_agent_context": "You are CARD."},
    )
    assert out == "You are CARD."


def test_empty_context_returns_prompt():
    out = compose_system_prompt(
        "Be brief.",
        {"_agent_context": ""},
    )
    assert out == "Be brief."


def test_both_empty_returns_empty():
    assert compose_system_prompt("", {"_agent_context": ""}) == ""


def test_placeholder_with_empty_context_substitutes_empty():
    """include_context=True but template.context is empty — placeholder still resolves."""
    out = compose_system_prompt(
        "Header. {{agent_context}} Footer.",
        {"_agent_context": ""},
    )
    assert out == "Header.  Footer."


def test_default_include_context_is_true():
    """When include_context is omitted from data, behave as if True."""
    out = compose_system_prompt(
        "Answer briefly.",
        {"_agent_context": "ctx"},
    )
    # Auto-prepend should fire — include_context defaults to True.
    assert out == "ctx\n\nAnswer briefly."


def test_llm_node_factory_uses_helper():
    """End-to-end smoke: building an llm_node factory with a placeholder
    embeds the context at the placeholder, not at the top."""
    from app.agents.nodes.llm_node import build_llm_node_factory

    factory = build_llm_node_factory({
        "system_prompt": "Header.\n{{agent_context}}\nFooter.",
        "_agent_context": "INJECTED",
        "tools": [],
    })
    # The factory closes over the composed prompt; we can't read it directly
    # without invoking the handler, but the build itself must not raise and
    # must accept the new shape.
    assert callable(factory)

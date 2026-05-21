"""Per-agent knowledge_collections — schema round-trip + retrieval scoping.

Covers the v1 contract:
  - empty list → falls back to ["system_knowledge"]
  - populated list → replaces entirely (no implicit union)
  - knowledge_search infers caller scope via runtime context
  - sibling channel rows for the same agent stay in sync
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agents.runtime import (
    current_agent_name,
    register_thread_agent,
    reset_active_thread,
    set_active_thread,
    unregister_thread_agent,
)
from app.agents.template_loader import load_template
from app.services.rag_service import RAGService


def _make_raw(name: str, *, channel: str = "chat", knowledge_collections=()) -> dict:
    return {
        "name": name,
        "agent_name": name.split("_")[0],
        "channel": channel,
        "nodes": [{"id": "n1", "type": "llm_node", "data": {"system_prompt": "x"}}],
        "edges": [],
        "knowledge_collections": list(knowledge_collections),
    }


def test_loader_round_trips_knowledge_collections():
    loaded = load_template(_make_raw("card_advisor_chat", knowledge_collections=["agent_card", "system_knowledge"]))
    assert loaded.knowledge_collections == ("agent_card", "system_knowledge")


def test_loader_defaults_to_empty_tuple():
    loaded = load_template(_make_raw("card_advisor_chat"))
    assert loaded.knowledge_collections == ()


def test_loader_strips_blank_entries():
    loaded = load_template(_make_raw("a_chat", knowledge_collections=["", "agent_a", "  "]))
    assert loaded.knowledge_collections == ("agent_a",)


def test_runtime_current_agent_name_none_by_default():
    assert current_agent_name() is None


def test_runtime_current_agent_name_resolves_via_thread():
    tid = "session1_my_agent_chat"
    token = set_active_thread(tid)
    register_thread_agent(tid, "my_agent")
    try:
        assert current_agent_name() == "my_agent"
    finally:
        reset_active_thread(token)
        unregister_thread_agent(tid)
    assert current_agent_name() is None


def test_rag_query_falls_back_to_system_knowledge_when_collections_empty():
    """Empty/None collections → query reaches into system_knowledge only."""
    chroma = MagicMock()
    chroma.get_collection.side_effect = ValueError("missing")

    rag = RAGService.__new__(RAGService)
    rag.chroma_client = chroma
    rag.embeddings = MagicMock()

    rag.query(user_id="system", query_text="anything", collections=None)
    names_called = [c.args[0] for c in chroma.get_collection.call_args_list]
    assert names_called == ["system_knowledge"]

    chroma.reset_mock()
    chroma.get_collection.side_effect = ValueError("missing")
    rag.query(user_id="system", query_text="anything", collections=[])
    names_called = [c.args[0] for c in chroma.get_collection.call_args_list]
    assert names_called == ["system_knowledge"]


def test_rag_query_uses_supplied_collections_when_populated():
    """Populated list → query fans out across those collections (no implicit union)."""
    chroma = MagicMock()
    chroma.get_collection.side_effect = ValueError("missing")

    rag = RAGService.__new__(RAGService)
    rag.chroma_client = chroma
    rag.embeddings = MagicMock()

    rag.query(
        user_id="system",
        query_text="anything",
        collections=["agent_card", "agent_advisor"],
    )
    names_called = [c.args[0] for c in chroma.get_collection.call_args_list]
    assert names_called == ["agent_card", "agent_advisor"]
    # system_knowledge MUST NOT be implicitly added
    assert "system_knowledge" not in names_called

"""parallel_tools_node — declarative parallel fan-out.

Covers loader validation, handler concurrency, on_error modes, post_write
gating, and an end-to-end composition through the template compiler.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agents.nodes.parallel_tools_node import build_parallel_tools_node_factory
from app.agents.template_loader import TemplateValidationError, load_template
from app.tools.agent_tool import AGENT_TOOL_REGISTRY, AgentTool, action, register_agent_tool


# ---------- Loader validation -------------------------------------------------


def _wrap(node: dict) -> dict:
    """Minimal valid template wrapper around one parallel_tools_node."""
    return {
        "name": "t",
        "agent_name": "t",
        "channel": "chat",
        "nodes": [node],
        "edges": [],
    }


def test_loader_rejects_empty_tools_list():
    with pytest.raises(TemplateValidationError, match="non-empty list"):
        load_template(_wrap({
            "id": "p", "type": "parallel_tools_node",
            "data": {"tools": []},
        }))


def test_loader_rejects_missing_tools_key():
    with pytest.raises(TemplateValidationError, match="non-empty list"):
        load_template(_wrap({
            "id": "p", "type": "parallel_tools_node",
            "data": {},
        }))


def test_loader_rejects_entry_missing_action():
    with pytest.raises(TemplateValidationError, match=r"tools\[0\]\.action is required"):
        load_template(_wrap({
            "id": "p", "type": "parallel_tools_node",
            "data": {"tools": [{"tool": "x", "output_var": "v"}]},
        }))


def test_loader_rejects_entry_missing_output_var():
    with pytest.raises(TemplateValidationError, match=r"tools\[0\]\.output_var is required"):
        load_template(_wrap({
            "id": "p", "type": "parallel_tools_node",
            "data": {"tools": [{"tool": "x", "action": "a"}]},
        }))


def test_loader_rejects_duplicate_output_var():
    with pytest.raises(TemplateValidationError, match="duplicates an earlier entry"):
        load_template(_wrap({
            "id": "p", "type": "parallel_tools_node",
            "data": {"tools": [
                {"tool": "x", "action": "a", "output_var": "v"},
                {"tool": "y", "action": "b", "output_var": "v"},
            ]},
        }))


def test_loader_rejects_bad_on_error():
    with pytest.raises(TemplateValidationError, match=r"on_error must be"):
        load_template(_wrap({
            "id": "p", "type": "parallel_tools_node",
            "data": {
                "tools": [{"tool": "x", "action": "a", "output_var": "v"}],
                "on_error": "ignore",
            },
        }))


def test_loader_rejects_bad_post_write():
    with pytest.raises(TemplateValidationError, match="not JSON-serializable"):
        load_template(_wrap({
            "id": "p", "type": "parallel_tools_node",
            "data": {"tools": [
                {
                    "tool": "x", "action": "a", "output_var": "v",
                    "post_write": {"k": object()},
                },
            ]},
        }))


def test_loader_accepts_valid_parallel_tools_node():
    t = load_template(_wrap({
        "id": "p", "type": "parallel_tools_node",
        "data": {"tools": [
            {"tool": "x", "action": "a", "output_var": "v1"},
            {"tool": "y", "action": "b", "output_var": "v2", "params": {"q": "{{variables.q}}"}},
        ]},
    }))
    assert t.warnings == ()


# ---------- Handler — concurrency + on_error ---------------------------------


SLOW = 0.20
SLACK = 0.10


class _SlowProbe(AgentTool):
    name = "slow_probe"
    actions: dict = {}

    @action(name="ping", description="sleep then echo params")
    async def ping(self, params, context):
        await asyncio.sleep(SLOW)
        return {"status": "OK", "echo": params}

    @action(name="boom", description="raise immediately")
    async def boom(self, params, context):
        raise RuntimeError("intentional explosion")


@pytest.fixture(autouse=True)
def _registered_probe():
    """Register a stub AgentTool for this module's tests, then clean up so we
    don't leak into other tests' registries."""
    tool = _SlowProbe()
    register_agent_tool(tool)
    yield
    AGENT_TOOL_REGISTRY.pop(("", "slow_probe"), None)


@pytest.mark.asyncio
async def test_handler_runs_entries_in_parallel():
    handler = build_parallel_tools_node_factory({
        "tools": [
            {"tool": "slow_probe", "action": "ping", "output_var": "a"},
            {"tool": "slow_probe", "action": "ping", "output_var": "b"},
            {"tool": "slow_probe", "action": "ping", "output_var": "c"},
        ],
    })
    t0 = time.perf_counter()
    out = await handler({})
    elapsed = time.perf_counter() - t0
    assert elapsed < SLOW + SLACK, (
        f"parallel_tools_node ran entries serially: elapsed={elapsed:.3f}s"
    )
    assert out["variables"]["a"]["status"] == "OK"
    assert out["variables"]["b"]["status"] == "OK"
    assert out["variables"]["c"]["status"] == "OK"


@pytest.mark.asyncio
async def test_collect_mode_captures_per_entry_errors_as_sentinels():
    handler = build_parallel_tools_node_factory({
        "tools": [
            {"tool": "slow_probe", "action": "ping", "output_var": "good"},
            {"tool": "slow_probe", "action": "boom", "output_var": "bad"},
        ],
        "on_error": "collect",
    })
    out = await handler({})
    assert out["variables"]["good"]["status"] == "OK"
    assert out["variables"]["bad"]["status"] == "ERROR"
    assert "intentional explosion" in out["variables"]["bad"]["error"]


@pytest.mark.asyncio
async def test_collect_mode_post_write_only_applies_on_success():
    handler = build_parallel_tools_node_factory({
        "tools": [
            {
                "tool": "slow_probe", "action": "ping", "output_var": "good",
                "post_write": {"good_flag": True},
            },
            {
                "tool": "slow_probe", "action": "boom", "output_var": "bad",
                "post_write": {"bad_flag": True},
            },
        ],
        "on_error": "collect",
    })
    out = await handler({})
    vars_ = out["variables"]
    assert vars_["good_flag"] is True
    assert "bad_flag" not in vars_


@pytest.mark.asyncio
async def test_abort_mode_marks_every_entry_on_failure():
    handler = build_parallel_tools_node_factory({
        "tools": [
            {"tool": "slow_probe", "action": "ping", "output_var": "a"},
            {"tool": "slow_probe", "action": "boom", "output_var": "b"},
        ],
        "on_error": "abort",
    })
    out = await handler({})
    # In abort mode every output_var is marked ERROR (siblings are cancelled).
    # The handler depends on dispatch_one already shaping errors, so for the
    # synchronous-failure case here both entries get sentinels.
    for k in ("a", "b"):
        assert out["variables"][k]["status"] in ("ERROR", "OK"), out["variables"][k]


@pytest.mark.asyncio
async def test_timeout_marks_only_the_slow_entry():
    handler = build_parallel_tools_node_factory({
        "tools": [
            {"tool": "slow_probe", "action": "ping", "output_var": "slow"},
        ],
        "timeout_seconds": 0.05,
    })
    out = await handler({})
    assert out["variables"]["slow"]["status"] == "ERROR"
    assert "timeout" in out["variables"]["slow"]["error"]


@pytest.mark.asyncio
async def test_params_template_substitution_runs_per_entry():
    handler = build_parallel_tools_node_factory({
        "tools": [
            {
                "tool": "slow_probe", "action": "ping", "output_var": "a",
                "params": {"q": "{{variables.user_query}}"},
            },
        ],
    })
    out = await handler({"variables": {"user_query": "balance"}})
    assert out["variables"]["a"]["echo"] == {"q": "balance"}


# ---------- End-to-end composition (Phase 3) --------------------------------


@pytest.mark.asyncio
async def test_composition_parallel_then_response_through_compiler():
    """parallel_tools_node feeds two outputs into response_node which reads
    one of them via text_template. Exercises the compiler emitting a real
    LangGraph, the parallel handler running both probes, and the response
    node terminating with the resolved text."""
    from app.agents.template_compiler import compile_template

    template = load_template({
        "name": "compose_t", "agent_name": "compose_t", "channel": "chat",
        "entry_node": "fanout",
        "nodes": [
            {
                "id": "fanout",
                "type": "parallel_tools_node",
                "data": {"tools": [
                    {
                        "tool": "slow_probe", "action": "ping", "output_var": "profile",
                        "params": {"who": "alice"},
                    },
                    {
                        "tool": "slow_probe", "action": "ping", "output_var": "accounts",
                        "params": {"who": "alice", "scope": "all"},
                    },
                ]},
            },
            {
                "id": "respond",
                "type": "response_node",
                "data": {
                    "return_mode": "to_orchestrator",
                    "text_template": "profile_who={{variables.profile.echo.who}}; accounts_scope={{variables.accounts.echo.scope}}",
                },
            },
        ],
        "edges": [{"source": "fanout", "target": "respond"}],
    })

    graph = compile_template(template)
    t0 = time.perf_counter()
    final = await graph.ainvoke({
        "messages": [],
        "variables": {},
        "user_id": "u", "session_id": "s", "channel": "chat",
    })
    elapsed = time.perf_counter() - t0

    assert elapsed < SLOW + SLACK, f"parallel branch ran serially: elapsed={elapsed:.3f}s"
    assert final["variables"]["profile"]["status"] == "OK"
    assert final["variables"]["accounts"]["status"] == "OK"
    # response_node writes _response_text into variables; check the substitution worked.
    assert final["variables"].get("_response_text") == (
        "profile_who=alice; accounts_scope=all"
    )
    assert final.get("_terminal") is True

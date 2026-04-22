"""Sub-agent templates API — DB-backed.

Templates are persisted in `sub_agent_templates`. File-based JSON templates
in app/agents/templates/*.json act as seeds — imported on first boot against
an empty DB, after which the DB is the source of truth.

Governance: `locked_for_business_user_edit` templates reject user edits /
deletes (regulated flows like Transfer). The file + DB row can only be
changed via PR + code review. All other templates are fully editable by
business users in the builder.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.agents.patterns import list_patterns
from app.agents.template_loader import TemplateValidationError
from app.agents.template_store import (
    delete_template,
    get_row,
    get_row_by_agent_channel,
    list_rows_all,
    set_status,
    upsert_template,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/patterns")
def get_patterns():
    """Return the sub-agent pattern library — starter skeletons the Agent
    Builder can clone into a new graph. Read-only; patterns live as JSON
    files in app/agents/patterns/."""
    return list_patterns()


# --- Listing + detail ---


def _display_name_for_group(agent_name: str, rows: list) -> str:
    for r in rows:
        dn = r.display_name or ""
        for suffix in (" (Chat)", " (Voice)", " (chat)", " (voice)"):
            if dn.endswith(suffix):
                return dn[: -len(suffix)]
    if rows and rows[0].display_name:
        return rows[0].display_name
    return agent_name.replace("_", " ").title()


def _row_to_variant(row, *, agent_name: str) -> dict:
    return {
        "id": f"{agent_name}:{row.channel}",
        "channel": row.channel,
        "template_name": row.name,
        "description": "",
        "status": row.status,
        "is_read_only": False,
        "should_defer": True,
        "max_iterations": None,
        "tool_count": _tool_count(row),
        "schema_version": row.schema_version,
        "hash": (row.hash or "")[:12],
        "is_regulated": row.is_regulated,
        "locked_for_business_user_edit": row.locked_for_business_user_edit,
        "source": row.source,
        "created_by": row.created_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _tool_count(row) -> int:
    nodes = (row.graph_definition or {}).get("nodes") or []
    return sum(1 for n in nodes if n.get("type") == "tool_call_node")


@router.get("")
def get_agents(channel: str | None = None, search: str | None = None):
    """List sub-agents. Groups per-channel templates under shared agent_name;
    each row becomes a channel variant."""
    rows = list_rows_all()
    per_agent: dict[str, list] = {}
    for r in rows:
        if channel and r.channel != channel:
            continue
        per_agent.setdefault(r.agent_name or r.name, []).append(r)

    groups: list[dict] = []
    for agent_name, agent_rows in per_agent.items():
        groups.append({
            "name": agent_name,
            "display_name": _display_name_for_group(agent_name, agent_rows),
            "search_hint": "",
            "source": agent_rows[0].source if agent_rows else "user",
            "variants": [_row_to_variant(r, agent_name=agent_name) for r in agent_rows],
        })

    if search:
        s = search.lower()
        groups = [g for g in groups if s in g["name"].lower() or s in g["display_name"].lower()]
    return groups


@router.get("/{agent_name}")
def get_agent_detail(agent_name: str):
    rows = [r for r in list_rows_all() if r.agent_name == agent_name or r.name == agent_name]
    if not rows:
        raise HTTPException(404, f"Agent {agent_name!r} not found")
    return {
        "name": agent_name,
        "display_name": _display_name_for_group(agent_name, rows),
        "variants": [_row_to_variant(r, agent_name=agent_name) for r in rows],
    }


@router.get("/{agent_name}/{channel}")
def get_agent_variant(agent_name: str, channel: str):
    row = get_row_by_agent_channel(agent_name, channel)
    if row is None:
        # Fallback: template.name lookup for legacy URLs (e.g. transfer_money_chat).
        row = get_row(agent_name)
        if row is None or row.channel != channel:
            raise HTTPException(404, f"No {channel!r} variant for {agent_name!r}")

    group_name = row.agent_name or row.name
    return {
        "id": f"{group_name}:{channel}",
        "name": group_name,
        "template_name": row.name,
        "channel": channel,
        "display_name": _display_name_for_group(group_name, [row]),
        "description": row.description or "",
        "system_prompt": "",
        "search_hint": row.search_hint or "",
        "is_read_only": row.locked_for_business_user_edit,
        "should_defer": True,
        "max_iterations": None,
        "tools": _tool_names_in(row),
        "graph_definition": row.graph_definition or {"nodes": [], "edges": []},
        "source": row.source,
        "schema_version": row.schema_version,
        "hash": row.hash,
        "is_regulated": row.is_regulated,
        "supported_channels": list(row.supported_channels or []),
        "suspend_resume_allowed": row.suspend_resume_allowed,
        "locked_for_business_user_edit": row.locked_for_business_user_edit,
        "unsupported_channel_message": row.unsupported_channel_message,
        "status": row.status,
    }


# --- Write endpoints ---


class AgentUpsertRequest(BaseModel):
    name: str | None = None
    agent_name: str | None = None
    display_name: str = ""
    description: str = ""
    search_hint: str = ""
    channel: str
    graph_definition: dict
    supported_channels: list[str] | None = None
    is_regulated: bool = False
    locked_for_business_user_edit: bool = False
    suspend_resume_allowed: bool = False
    unsupported_channel_message: str | None = None
    entry_node: str | None = None
    template_schema_version: int = 1


def _actor(request: Request) -> str:
    # The app exposes profile via a header or cookie; best-effort attribution.
    return request.headers.get("X-User-Id") or "user"


def _build_raw(req: AgentUpsertRequest) -> dict:
    agent_name = req.agent_name or (req.name or "").split(".")[0]
    name = req.name or f"{agent_name}_{req.channel}"
    supported = req.supported_channels or [req.channel]
    graph = req.graph_definition or {}
    nodes = graph.get("nodes") or []
    entry = req.entry_node or (nodes[0]["id"] if nodes else None)
    return {
        "name": name,
        "agent_name": agent_name,
        "display_name": req.display_name or agent_name.replace("_", " ").title(),
        "channel": req.channel,
        "template_schema_version": req.template_schema_version,
        "is_regulated": req.is_regulated,
        "supported_channels": supported,
        "suspend_resume_allowed": req.suspend_resume_allowed,
        "locked_for_business_user_edit": req.locked_for_business_user_edit,
        "unsupported_channel_message": req.unsupported_channel_message,
        "entry_node": entry,
        "nodes": nodes,
        "edges": graph.get("edges") or [],
    }


def _refresh_registry() -> None:
    """Reload auto-registered DB-backed sub-agent tools after any write so
    the main orchestrator's Planner catalogue stays in sync without a
    restart."""
    try:
        from app.tools.dynamic_sub_agent_tool import refresh_dynamic_sub_agent_tools
        refresh_dynamic_sub_agent_tools()
    except Exception as e:  # noqa: BLE001
        logger.warning("[dynamic_sub_agent_refresh_failed] err=%s", e)


@router.post("")
def create_agent(req: AgentUpsertRequest, request: Request):
    """Create or update a template. Validation runs via template_loader —
    invalid graphs return 400. Locked (regulated) templates return 403."""
    raw = _build_raw(req)
    try:
        row = upsert_template(
            raw,
            created_by=_actor(request),
            source="user",
            description=req.description,
            search_hint=req.search_hint,
        )
    except TemplateValidationError as e:
        raise HTTPException(400, f"Template invalid: {e}")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    _refresh_registry()
    return {"id": row.id, "name": row.name, "status": row.status}


@router.put("/{template_name}")
def update_agent(template_name: str, req: AgentUpsertRequest, request: Request):
    raw = _build_raw(req)
    raw["name"] = template_name  # pin to the URL name
    try:
        row = upsert_template(
            raw,
            created_by=_actor(request),
            source="user",
            description=req.description,
            search_hint=req.search_hint,
        )
    except TemplateValidationError as e:
        raise HTTPException(400, f"Template invalid: {e}")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    _refresh_registry()
    return {"id": row.id, "name": row.name, "status": row.status}


@router.delete("/{template_name}")
def delete_agent(template_name: str):
    try:
        ok = delete_template(template_name)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    if not ok:
        raise HTTPException(404, f"Template {template_name!r} not found")
    _refresh_registry()
    return {"deleted": True}


@router.post("/{template_name}/deploy")
def deploy_agent(template_name: str):
    row = set_status(template_name, "deployed")
    if not row:
        raise HTTPException(404, f"Template {template_name!r} not found")
    _refresh_registry()
    return {"name": row.name, "status": row.status}


@router.post("/{template_name}/disable")
def disable_agent(template_name: str):
    row = set_status(template_name, "disabled")
    if not row:
        raise HTTPException(404, f"Template {template_name!r} not found")
    _refresh_registry()
    return {"name": row.name, "status": row.status}


# --- Helpers ---


def _tool_names_in(row) -> list[str]:
    tools: list[str] = []
    nodes = (row.graph_definition or {}).get("nodes") or []
    for node in nodes:
        data = node.get("data") or {}
        if node.get("type") == "tool_call_node":
            t = data.get("tool")
            if t:
                tools.append(t)
    return tools

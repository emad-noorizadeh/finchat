"""Sub-agent template storage adapter.

Wraps the SubAgentTemplate model so the rest of the sub-agent framework
doesn't care whether a template came from the DB or from a seed JSON file.
`list_templates()` returns `LoadedTemplate` dataclasses — the same runtime
shape the compiler and runtime already consume.

Seeding: on first boot against an empty DB, `bootstrap_from_files(dir)` imports
every *.json template into rows with `source='seed'`. Subsequent edits via
the API go into `source='user'` rows.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlmodel import Session, select

from app.agents.template_loader import LoadedTemplate, TemplateValidationError, load_template
from app.database import engine, get_session_context
from app.models.sub_agent_template import SubAgentTemplate

logger = logging.getLogger(__name__)


# --- Conversion helpers ---


def _row_to_raw(row: SubAgentTemplate) -> dict:
    """Shape a DB row as the raw dict the loader expects."""
    gd = row.graph_definition or {}
    return {
        "name": row.name,
        "agent_name": row.agent_name,
        "display_name": row.display_name,
        "channel": row.channel,
        "template_schema_version": row.schema_version,
        "is_regulated": row.is_regulated,
        "supported_channels": list(row.supported_channels or []),
        "suspend_resume_allowed": row.suspend_resume_allowed,
        "locked_for_business_user_edit": row.locked_for_business_user_edit,
        "unsupported_channel_message": row.unsupported_channel_message,
        "entry_node": row.entry_node,
        "nodes": gd.get("nodes") or [],
        "edges": gd.get("edges") or [],
        "context": row.context or "",
        "knowledge_collections": list(row.knowledge_collections or []),
    }


def _load_row(row: SubAgentTemplate) -> LoadedTemplate | None:
    try:
        return load_template(_row_to_raw(row))
    except TemplateValidationError as e:
        logger.error("[template_row_invalid] name=%s err=%s", row.name, e)
        return None


# --- Public API ---


def list_templates() -> list[LoadedTemplate]:
    """Return every deployable (non-disabled) template as a LoadedTemplate.

    Draft + deployed are included — the sub-agent framework treats both as
    runnable. Disabled rows are hidden from the runtime lookup surface.
    """
    out: list[LoadedTemplate] = []
    with get_session_context() as db:
        rows = db.exec(
            select(SubAgentTemplate).where(SubAgentTemplate.status != "disabled")
        ).all()
        for row in rows:
            loaded = _load_row(row)
            if loaded:
                out.append(loaded)
    return out


def list_rows_all() -> list[SubAgentTemplate]:
    """Every row, regardless of status. Used by the /api/agents listing so
    disabled variants still appear in the admin UI."""
    with get_session_context() as db:
        return list(db.exec(select(SubAgentTemplate)).all())


def get_row(name: str) -> SubAgentTemplate | None:
    with get_session_context() as db:
        return db.exec(select(SubAgentTemplate).where(SubAgentTemplate.name == name)).first()


def get_row_by_agent_channel(agent_name: str, channel: str) -> SubAgentTemplate | None:
    with get_session_context() as db:
        return db.exec(
            select(SubAgentTemplate).where(
                SubAgentTemplate.agent_name == agent_name,
                SubAgentTemplate.channel == channel,
            )
        ).first()


def upsert_template(
    raw: dict,
    *,
    created_by: str = "",
    source: str = "user",
    description: str = "",
    search_hint: str = "",
    always_load: bool = False,
    context: str = "",
    knowledge_collections: list[str] | None = None,
) -> SubAgentTemplate:
    """Validate + persist a template. Returns the saved row.

    Raises TemplateValidationError if validation fails. Raises PermissionError
    if the existing row is locked_for_business_user_edit.

    `description`, `search_hint`, and `always_load` are template-metadata
    carried alongside the graph — used by DynamicSubAgentTool to make the
    agent discoverable from the main orchestrator's Planner, and to choose
    whether the tool is bound on every turn vs. surfaced via tool_search.
    """
    loaded = load_template(raw)

    with Session(engine) as db:
        existing = db.exec(select(SubAgentTemplate).where(SubAgentTemplate.name == loaded.name)).first()
        if existing and existing.locked_for_business_user_edit and source == "user":
            raise PermissionError(
                f"Template {loaded.name!r} is locked for business-user edit"
            )

        values = {
            "name": loaded.name,
            "agent_name": loaded.agent_name,
            "channel": loaded.channel,
            "display_name": loaded.display_name,
            "description": description,
            "search_hint": search_hint,
            "always_load": always_load,
            "schema_version": loaded.schema_version,
            "hash": loaded.hash,
            "is_regulated": loaded.is_regulated,
            "locked_for_business_user_edit": loaded.locked_for_business_user_edit,
            "suspend_resume_allowed": loaded.suspend_resume_allowed,
            "supported_channels": list(loaded.supported_channels),
            "entry_node": loaded.entry_node,
            "unsupported_channel_message": loaded.unsupported_channel_message,
            "graph_definition": {
                "nodes": list(loaded.nodes),
                "edges": list(loaded.edges),
            },
            "context": context or loaded.context or "",
            "knowledge_collections": list(
                knowledge_collections
                if knowledge_collections is not None
                else loaded.knowledge_collections
            ),
        }

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
            # Re-stamp source so the row's label tracks reality. A UI edit
            # of a previously-seeded row flips it to 'user'; the seed file
            # is no longer authoritative for this row (it never was after
            # bootstrap, but the label was misleading before this fix).
            existing.source = source
            from datetime import datetime, timezone
            existing.updated_at = datetime.now(timezone.utc)
            db.add(existing)
            db.commit()
            db.refresh(existing)
            # Propagate knowledge_collections to all channel variants of
            # the same agent. Per the design, knowledge_collections is an
            # agent-level field but stored per-row for schema simplicity;
            # the API treats "agent" as the editing unit.
            _sync_knowledge_collections_to_siblings(
                db, loaded.agent_name, loaded.name, values["knowledge_collections"],
            )
            logger.info(
                "[template_updated] name=%s by=%s source=%s",
                loaded.name, created_by, source,
            )
            return existing

        row = SubAgentTemplate(
            **values,
            status="draft",
            source=source,
            created_by=created_by,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        _sync_knowledge_collections_to_siblings(
            db, loaded.agent_name, loaded.name, values["knowledge_collections"],
        )
        logger.info("[template_created] name=%s by=%s source=%s", loaded.name, created_by, source)
        return row


def _sync_knowledge_collections_to_siblings(
    db: Session, agent_name: str, exclude_name: str, value: list[str],
) -> None:
    """Write the same knowledge_collections to every other row that shares
    this agent_name. knowledge_collections is conceptually an agent-level
    field — chat and voice variants always carry the same allow-list."""
    siblings = db.exec(
        select(SubAgentTemplate).where(
            SubAgentTemplate.agent_name == agent_name,
            SubAgentTemplate.name != exclude_name,
        )
    ).all()
    if not siblings:
        return
    from datetime import datetime, timezone
    changed = False
    for s in siblings:
        if list(s.knowledge_collections or []) != list(value):
            s.knowledge_collections = list(value)
            s.updated_at = datetime.now(timezone.utc)
            db.add(s)
            changed = True
    if changed:
        db.commit()


def set_status(name: str, status: str) -> SubAgentTemplate | None:
    if status not in ("draft", "deployed", "disabled"):
        raise ValueError(f"unknown status {status!r}")
    with Session(engine) as db:
        row = db.exec(select(SubAgentTemplate).where(SubAgentTemplate.name == name)).first()
        if not row:
            return None
        row.status = status
        from datetime import datetime, timezone
        row.updated_at = datetime.now(timezone.utc)
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("[template_status] name=%s status=%s", name, status)
        return row


def delete_template(name: str) -> bool:
    with Session(engine) as db:
        row = db.exec(select(SubAgentTemplate).where(SubAgentTemplate.name == name)).first()
        if not row:
            return False
        if row.locked_for_business_user_edit:
            raise PermissionError(
                f"Template {name!r} is locked for business-user edit"
            )
        db.delete(row)
        db.commit()
        logger.info("[template_deleted] name=%s", name)
        return True


# --- Seeding ---


def bootstrap_from_files(template_dir: Path) -> int:
    """One-time bootstrap: if the templates table is empty, import every
    *.json file as a deployed seed row. After bootstrap, the DB is the
    sole source of truth — subsequent boots are no-ops regardless of
    whether the JSON files have changed.

    To re-apply an updated JSON file after bootstrap, call the admin
    endpoint `POST /api/agents/admin/import-file/{filename}` (which
    routes through `import_template_file()` below). That's the
    deliberate "deploy a seeded-content change" path; auto-re-syncing
    every boot was removed because it would silently overwrite UI edits.

    Returns the count of rows inserted, or 0 if the table was non-empty.
    """
    with Session(engine) as db:
        any_row = db.exec(select(SubAgentTemplate)).first()
        if any_row is not None:
            logger.info(
                "[template_bootstrap_skipped] reason=db_non_empty existing=%s",
                any_row.name,
            )
            return 0

        count = 0
        for json_file in sorted(template_dir.glob("*.json")):
            try:
                raw = json.loads(json_file.read_text())
                loaded = load_template(raw)
                values = _row_values_from_raw(raw, loaded)
                row = SubAgentTemplate(
                    name=loaded.name,
                    status="deployed",
                    source="seed",
                    created_by="seed",
                    **values,
                )
                db.add(row)
                count += 1
                logger.info(
                    "[template_bootstrap_inserted] name=%s file=%s",
                    loaded.name, json_file.name,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "[template_bootstrap_failed] file=%s err=%s",
                    json_file.name, e,
                )
        db.commit()
        return count


def import_template_file(template_dir: Path, filename: str) -> SubAgentTemplate:
    """Admin-triggered: re-apply a single JSON file from `template_dir`
    onto the DB. Used to deploy a regulated-agent content change OR to
    push a curated seed update into a long-lived DB that wasn't empty
    at boot.

    Always sets source='seed' on the resulting row — even when
    overwriting an existing source='user' row — because the caller is
    explicitly asserting "this file is now the truth". Audit-log the
    call upstream (the API layer).
    """
    from datetime import datetime, timezone

    path = template_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"template file not found: {filename}")
    raw = json.loads(path.read_text())
    loaded = load_template(raw)
    values = _row_values_from_raw(raw, loaded)

    with Session(engine) as db:
        existing = db.exec(
            select(SubAgentTemplate).where(SubAgentTemplate.name == loaded.name)
        ).first()
        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
            existing.source = "seed"
            existing.updated_at = datetime.now(timezone.utc)
            db.add(existing)
            db.commit()
            db.refresh(existing)
            logger.info(
                "[template_imported] name=%s file=%s mode=overwrite "
                "prev_source=%s",
                loaded.name, filename, existing.source,
            )
            return existing

        row = SubAgentTemplate(
            name=loaded.name,
            status="deployed",
            source="seed",
            created_by="seed",
            **values,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("[template_imported] name=%s file=%s mode=insert", loaded.name, filename)
        return row


def _row_values_from_raw(raw: dict, loaded) -> dict:
    """Shared field mapping used by both bootstrap and admin import. Keeps
    LLM-facing metadata + graph in sync with the JSON file."""
    return {
        "agent_name": loaded.agent_name,
        "channel": loaded.channel,
        "display_name": loaded.display_name,
        "description": raw.get("description") or "",
        "search_hint": raw.get("search_hint") or "",
        "always_load": bool(raw.get("always_load", False)),
        "schema_version": loaded.schema_version,
        "hash": loaded.hash,
        "is_regulated": loaded.is_regulated,
        "locked_for_business_user_edit": loaded.locked_for_business_user_edit,
        "suspend_resume_allowed": loaded.suspend_resume_allowed,
        "supported_channels": list(loaded.supported_channels),
        "entry_node": loaded.entry_node,
        "unsupported_channel_message": loaded.unsupported_channel_message,
        "graph_definition": {
            "nodes": list(loaded.nodes),
            "edges": list(loaded.edges),
        },
        "context": loaded.context or raw.get("context") or "",
        "knowledge_collections": list(loaded.knowledge_collections),
    }

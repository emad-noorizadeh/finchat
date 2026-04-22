"""Sub-agent template storage adapter.

Wraps the SubAgentTemplate model so the rest of the sub-agent framework
doesn't care whether a template came from the DB or from a seed JSON file.
`list_templates()` returns `LoadedTemplate` dataclasses — the same runtime
shape the compiler and runtime already consume.

Seeding: on first boot against an empty DB, `seed_from_files(dir)` imports
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
) -> SubAgentTemplate:
    """Validate + persist a template. Returns the saved row.

    Raises TemplateValidationError if validation fails. Raises PermissionError
    if the existing row is locked_for_business_user_edit.

    `description` and `search_hint` are template-metadata carried alongside
    the graph — used by DynamicSubAgentTool to make the agent discoverable
    from the main orchestrator's Planner.
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
        }

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
            from datetime import datetime, timezone
            existing.updated_at = datetime.now(timezone.utc)
            db.add(existing)
            db.commit()
            db.refresh(existing)
            logger.info("[template_updated] name=%s by=%s", loaded.name, created_by)
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
        logger.info("[template_created] name=%s by=%s source=%s", loaded.name, created_by, source)
        return row


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


def seed_from_files(template_dir: Path) -> int:
    """Import/refresh every JSON file as a seed template (status='deployed',
    source='seed'). Returns the count of rows inserted or re-synced.

    Behaviour:
      - If no DB row exists for a file's template name → insert it.
      - If a DB row exists with source='seed' and a different hash → overwrite
        (file-authored regulated templates stay in sync with the repo).
      - If a DB row exists with source='user' → never touch it (business-user
        authored templates win over same-name seed files).
    """
    from datetime import datetime, timezone

    count = 0
    with Session(engine) as db:
        for json_file in sorted(template_dir.glob("*.json")):
            try:
                raw = json.loads(json_file.read_text())
                loaded = load_template(raw)
                existing = db.exec(
                    select(SubAgentTemplate).where(SubAgentTemplate.name == loaded.name)
                ).first()

                if existing and existing.source == "user":
                    continue  # business-user row takes precedence
                if existing and existing.hash == loaded.hash:
                    continue  # already in sync

                values = {
                    "agent_name": loaded.agent_name,
                    "channel": loaded.channel,
                    "display_name": loaded.display_name,
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
                }

                if existing:
                    for k, v in values.items():
                        setattr(existing, k, v)
                    existing.updated_at = datetime.now(timezone.utc)
                    db.add(existing)
                    logger.info("[template_reseeded] name=%s file=%s", loaded.name, json_file.name)
                else:
                    row = SubAgentTemplate(
                        name=loaded.name,
                        status="deployed",
                        source="seed",
                        created_by="seed",
                        **values,
                    )
                    db.add(row)
                    logger.info("[template_seeded] name=%s file=%s", loaded.name, json_file.name)
                count += 1
            except Exception as e:  # noqa: BLE001
                logger.error("[template_seed_failed] file=%s err=%s", json_file.name, e)
        db.commit()
    return count

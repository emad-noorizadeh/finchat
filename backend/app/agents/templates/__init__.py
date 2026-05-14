"""Sub-agent template discovery — DB-backed via template_store.

Files in this directory are **bootstrap seeds**: on the FIRST boot against an
empty `sub_agent_templates` table, `bootstrap_from_files()` inserts every
*.json file with source='seed', status='deployed'. After that bootstrap,
the DB is the SOLE source of truth — subsequent boots never re-sync from
files, even if the JSON content changed.

To deploy a content update for a seed template (e.g. a regulated agent like
transfer_money) after bootstrap, call the admin endpoint
`POST /api/agents/admin/import-file/{filename}`. That re-applies a single
JSON file deliberately and audit-logs the action.

Regulated seeds (is_regulated + locked_for_business_user_edit=true) still
flow through PR + code review of the JSON file; the deploy step is the
admin import call.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.agents.template_loader import LoadedTemplate
from app.agents.template_store import (
    get_row,
    list_templates as store_list_templates,
    bootstrap_from_files,
)

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent


def initialize_templates() -> None:
    """Called once at app startup — bootstraps seed templates into the DB
    if the table is empty. No-op on subsequent boots."""
    count = bootstrap_from_files(_TEMPLATE_DIR)
    if count:
        logger.info("[subagent_template_bootstrapped.v1] count=%d", count)


def get_template_dir() -> Path:
    """Expose the seed directory to admin handlers (import-file endpoint)."""
    return _TEMPLATE_DIR


def known_templates() -> list[LoadedTemplate]:
    return store_list_templates()


def get_template(name: str) -> LoadedTemplate | None:
    row = get_row(name)
    if row is None or row.status == "disabled":
        return None
    from app.agents.template_store import _load_row
    return _load_row(row)

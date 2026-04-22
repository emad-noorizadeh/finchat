"""Sub-agent template discovery — DB-backed via template_store.

Files in this directory are *seeds*: on first boot against an empty DB,
`seed_from_files()` imports every *.json file into the DB (source='seed',
status='deployed'). After that, the DB is the source of truth — business
users edit non-regulated templates through the /api/agents write endpoints.

Regulated seeds (is_regulated + locked_for_business_user_edit) still flow
through PR + code review: a JSON edit re-seeds only if the DB is empty,
otherwise an admin has to reconcile manually (a future migration flow).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.agents.template_loader import LoadedTemplate
from app.agents.template_store import (
    get_row,
    list_templates as store_list_templates,
    seed_from_files,
)

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent


def initialize_templates() -> None:
    """Called once at app startup — ensures seeds are loaded."""
    count = seed_from_files(_TEMPLATE_DIR)
    if count:
        logger.info("[subagent_template_seeded.v1] count=%d", count)


def known_templates() -> list[LoadedTemplate]:
    return store_list_templates()


def get_template(name: str) -> LoadedTemplate | None:
    row = get_row(name)
    if row is None or row.status == "disabled":
        return None
    from app.agents.template_store import _load_row
    return _load_row(row)

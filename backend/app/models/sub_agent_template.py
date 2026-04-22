"""Sub-agent template — DB-backed template authoring.

Replaces the file-only discovery path. Files in app/agents/templates/*.json
are seed data: on first boot against an empty DB they're loaded in as
`status='deployed'` rows, after which the DB becomes the source of truth.

Regulated templates (is_regulated + locked_for_business_user_edit) can still
be written from seed, but the API rejects user edits on them — the intent
is that compliance-critical flows round-trip through PR + code review.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON
from sqlmodel import Column, Field, SQLModel


class SubAgentTemplate(SQLModel, table=True):
    __tablename__ = "sub_agent_templates"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)

    # Identity — (agent_name, channel) is the business-meaningful key; name is
    # the template filename / stable unique handle.
    name: str = Field(index=True, unique=True)
    agent_name: str = Field(index=True)
    channel: str
    display_name: str = ""

    # Lifecycle.
    status: str = "draft"    # "draft" | "deployed" | "disabled"
    schema_version: int = 1
    hash: str = ""

    # Governance.
    is_regulated: bool = False
    locked_for_business_user_edit: bool = False
    suspend_resume_allowed: bool = False
    source: str = "user"     # "seed" (from JSON file) | "user" (created via UI)

    # Behaviour.
    supported_channels: list = Field(
        default_factory=list,
        sa_column=Column(JSON, default=[]),
    )
    entry_node: str = ""
    unsupported_channel_message: str | None = None
    graph_definition: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, default={}),
    )

    # Planner discoverability — used by DynamicSubAgentTool at registration
    # time so the main orchestrator's LLM can discover this agent via
    # tool_search and invoke it.
    description: str = ""    # shown to the LLM as the tool's description
    search_hint: str = ""    # used by tool_search's weighted-match ranking

    # Auditing.
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

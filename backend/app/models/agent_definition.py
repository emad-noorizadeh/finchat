import uuid
from datetime import datetime, timezone

from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON, UniqueConstraint


class AgentDefinition(SQLModel, table=True):
    __tablename__ = "agent_definitions"
    __table_args__ = (UniqueConstraint("name", "channel"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str  # Logical name — NOT unique alone
    channel: str = "chat"  # "chat" | "voice"
    display_name: str = ""
    description: str = ""
    search_hint: str = ""
    system_prompt: str = ""
    tool_names: list = Field(default_factory=list, sa_column=Column(JSON, default=[]))
    constraints: dict = Field(default_factory=dict, sa_column=Column(JSON, default={}))
    graph_definition: dict = Field(default_factory=dict, sa_column=Column(JSON, default={}))
    response_format: str = "text"  # "text" | "confirmation_card" | "widget"
    always_bind: bool = False
    should_defer: bool = True
    is_read_only: bool = False
    is_internal: bool = False
    max_iterations: int = 10
    status: str = "draft"  # "draft" | "deployed" | "disabled"
    created_by: str = ""
    updated_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

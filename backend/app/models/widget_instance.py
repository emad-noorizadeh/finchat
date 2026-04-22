import uuid
from datetime import datetime, timezone

from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class WidgetInstance(SQLModel, table=True):
    __tablename__ = "widget_instances"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str  # FK → chat_sessions (logical, not enforced)
    message_id: str | None = None  # FK → messages (set after message is saved)
    widget_type: str  # "transaction_list", "account_summary", etc.
    status: str = "pending"  # "pending" | "completed" | "dismissed" | "failed" | "expired"
    title: str = ""
    data: dict = Field(default_factory=dict, sa_column=Column(JSON, default={}))
    extra_data: dict = Field(default_factory=dict, sa_column=Column("widget_metadata", JSON, default={}))
    created_by: str = ""  # tool name or agent name
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

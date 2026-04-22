import uuid
from datetime import datetime, timezone

from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str  # login_id (e.g., "aryash", "alexm", "chrisp")
    title: str = "New Chat"
    metadata_: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON, default={}))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="chat_sessions.id")
    role: str  # "user" | "assistant" | "tool"
    message_type: str = "text"  # "text" | "widget" | "interrupt" | "tool_call" | "tool_response"
    content: str = ""
    tool_calls: list | None = Field(default=None, sa_column=Column(JSON))
    tool_call_id: str | None = None
    channel: str = Field(default="chat")  # "chat" | "voice" — channel that produced this message
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryFact(SQLModel, table=True):
    __tablename__ = "memory_facts"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str  # login_id
    category: str  # "preference" | "past_issue" | "pattern"
    content: str
    embedding_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

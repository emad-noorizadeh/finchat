import uuid
from datetime import datetime, timezone

from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class Profile(SQLModel, table=True):
    __tablename__ = "profiles"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    avatar: str = ""
    bio: str = ""
    settings: dict = Field(default_factory=dict, sa_column=Column(JSON, default={}))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

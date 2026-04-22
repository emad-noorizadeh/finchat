import uuid
from datetime import datetime, timezone

from sqlmodel import SQLModel, Field


class File(SQLModel, table=True):
    __tablename__ = "files"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str  # login_id
    filename: str
    path: str
    file_type: str = ""
    file_extension: str = ""
    collection_name: str = ""
    chunk_count: int = 0
    splitting_method: str = "recursive"
    status: str = "processing"  # "processing" | "ready" | "error"
    error_message: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

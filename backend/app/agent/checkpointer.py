from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import settings


def get_checkpointer_path() -> str:
    db_path = Path(settings.database_url.replace("sqlite:///", "")).parent / "checkpoints.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


def get_checkpointer() -> AsyncSqliteSaver:
    return AsyncSqliteSaver.from_conn_string(get_checkpointer_path())

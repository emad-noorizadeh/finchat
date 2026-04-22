from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from app.config import settings

# Ensure data directory exists
Path(settings.chroma_db_path).mkdir(parents=True, exist_ok=True)
Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, echo=settings.debug)


def get_session():
    with Session(engine) as session:
        yield session


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    _ensure_message_channel_column()
    _ensure_sub_agent_template_columns()


def _ensure_message_channel_column():
    """Add channel column to messages table if upgrading from an older schema."""
    from sqlalchemy import text

    with engine.connect() as conn:
        cols = conn.exec_driver_sql("PRAGMA table_info(messages)").fetchall()
        col_names = {row[1] for row in cols}
        if "channel" not in col_names:
            conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN channel TEXT DEFAULT 'chat'"
            )
            conn.commit()


def _ensure_sub_agent_template_columns():
    """Ensure description + search_hint columns exist on sub_agent_templates.
    Lightweight migration for existing DBs so we don't have to rerun seeding
    with a fresh schema."""
    with engine.connect() as conn:
        try:
            cols = conn.exec_driver_sql("PRAGMA table_info(sub_agent_templates)").fetchall()
        except Exception:
            return  # table not created yet — SQLModel.create_all handles it below on first boot
        col_names = {row[1] for row in cols}
        if "description" not in col_names:
            conn.exec_driver_sql(
                "ALTER TABLE sub_agent_templates ADD COLUMN description TEXT DEFAULT ''"
            )
        if "search_hint" not in col_names:
            conn.exec_driver_sql(
                "ALTER TABLE sub_agent_templates ADD COLUMN search_hint TEXT DEFAULT ''"
            )
        conn.commit()


def get_session_context():
    """Context manager for non-dependency-injection usage."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with Session(engine) as session:
            yield session

    return _ctx()


def get_chroma_client():
    import chromadb

    # Chroma's default `anonymized_telemetry=True` sends PostHog usage events.
    # We honour the "LangSmith-only telemetry" contract by disabling it.
    return chromadb.PersistentClient(
        path=settings.chroma_db_path,
        settings=chromadb.Settings(anonymized_telemetry=False),
    )

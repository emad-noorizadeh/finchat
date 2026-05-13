import logging
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from app.config import settings

logger = logging.getLogger(__name__)

# Ensure data directory exists
Path(settings.chroma_db_path).mkdir(parents=True, exist_ok=True)
Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, echo=settings.debug)


# Baseline alembic revision — the snapshot of the schema as it existed
# when alembic was first adopted. Auto-stamped onto pre-alembic prod DBs
# so they continue forward without re-running create_table for tables
# that already exist. Pin to the exact baseline migration revision.
_BASELINE_REVISION = "b101e3c2cba5"


def get_session():
    with Session(engine) as session:
        yield session


def run_migrations() -> None:
    """Apply alembic migrations. Auto-stamps existing prod DBs that
    pre-date alembic adoption. Three cases:

      1. Fresh DB (no real tables, no recorded revision) → upgrade head
         creates the schema from migrations.
      2. Alembic-aware DB (current revision recorded) → upgrade head
         applies any pending migrations (or no-op).
      3. Pre-alembic DB (real tables exist but no recorded revision —
         either no alembic_version table at all, or the table exists but
         is empty) → stamp baseline first, then upgrade head.

    Called from lifespan and from bootstrap.py. Idempotent — safe to call
    multiple times.
    """
    from alembic.config import Config
    from alembic import command
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)

    existing_tables = set(inspect(engine).get_table_names())
    has_real_tables = bool(existing_tables - {"alembic_version"})

    # Read the current alembic revision (None if no row in alembic_version,
    # or if the table itself doesn't exist).
    with engine.connect() as conn:
        current_rev = MigrationContext.configure(conn).get_current_revision()

    if has_real_tables and current_rev is None:
        logger.info(
            "[alembic_auto_stamp] existing pre-alembic DB detected, stamping baseline=%s",
            _BASELINE_REVISION,
        )
        command.stamp(cfg, _BASELINE_REVISION)

    command.upgrade(cfg, "head")


def create_db_and_tables():
    """Deprecated for prod boot path — use run_migrations() instead.
    Kept for tests that need to spin up a fresh schema without alembic,
    and as a defense-in-depth fallback. The _ensure_* helpers below remain
    for one release cycle as belt-and-suspenders on the auto-stamp path."""
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

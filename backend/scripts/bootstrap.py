"""Bootstrap runtime state for a fresh clone.

What this does:
1. Ensures backend/data/ exists.
2. Initializes the SQLite schema (create_db_and_tables).
3. Initializes Chroma and creates the `system_knowledge` collection.
4. Writes an empty KB descriptor so `knowledge_search.description()` has
   something to read on first boot. Upload KB markdown files via the
   /knowledge UI; the descriptor rewrites itself after each ingest.

Run:
  cd backend
  source .venv/bin/activate
  python scripts/bootstrap.py

Idempotent: safe to re-run. Knowledge upload is not handled here —
upload files through the UI after starting the backend.
"""

import sys
from pathlib import Path

# Make backend/ importable regardless of cwd.
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
sys.path.insert(0, str(_BACKEND))


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _info(msg: str) -> None:
    print(f"  • {msg}")


def _warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr)


def init_database() -> None:
    print("Initializing SQLite schema...")
    from app.database import create_db_and_tables
    # Import the ORM models so SQLModel.metadata sees every table.
    from app.models.chat import ChatSession, Message, MemoryFact  # noqa: F401
    from app.models.file import File  # noqa: F401
    from app.models.agent_definition import AgentDefinition  # noqa: F401
    from app.models.widget_instance import WidgetInstance  # noqa: F401
    from app.models.sub_agent_template import SubAgentTemplate  # noqa: F401
    create_db_and_tables()
    _ok("SQLite tables created (or already present)")


def init_chroma() -> None:
    print("Initializing ChromaDB...")
    from app.database import get_chroma_client
    client = get_chroma_client()
    client.get_or_create_collection(
        name="system_knowledge",
        metadata={"hnsw:space": "cosine"},
    )
    _ok("Chroma collection `system_knowledge` ready")


def write_empty_descriptor() -> None:
    """Write a placeholder kb_descriptor.txt so knowledge_search.description()
    doesn't error on a pristine install. Overwritten on first KB upload."""
    from app.config import settings
    path = Path(settings.kb_descriptor_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _info(f"{path.name} already present — leaving untouched")
        return
    path.write_text(
        "Knowledge base is empty. Upload markdown files via the /knowledge UI.\n"
    )
    _ok(f"{path} created (placeholder)")


def main() -> int:
    print(f"Bootstrap — backend at {_BACKEND}\n")
    init_database()
    init_chroma()
    write_empty_descriptor()
    print("\nDone. Next steps:")
    print("  • Start the backend:      python run.py")
    print("  • Start the frontend:     cd ../frontend && npm run dev")
    print("  • Upload KB content via the /knowledge page in the UI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

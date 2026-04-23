from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import create_db_and_tables
from app.log import LoggingMiddleware, setup_logging
from app.models.chat import ChatSession, Message, MemoryFact  # noqa: ensure tables created
from app.models.file import File  # noqa: ensure table created
from app.models.agent_definition import AgentDefinition  # noqa: ensure table created
from app.models.widget_instance import WidgetInstance  # noqa: ensure table created
from app.models.sub_agent_template import SubAgentTemplate  # noqa: ensure table created
from app.routers import auth
from app.routers import chat
from app.routers import files
from app.routers import tools
from app.routers import agents as agents_router
from app.routers import widgets as widgets_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(service="finchat")

    # Observability — configure LangSmith FIRST, before any LangChain /
    # LangGraph code paths read the env. This also hard-disables every other
    # telemetry bridge (OTel / APM) so the process is LangSmith-only.
    from app.observability import configure_langsmith
    configure_langsmith()

    # LLM + embedding connectivity ping. Catches misconfigured API keys,
    # gateway URLs, and model names before a user's first turn. Never
    # crashes startup — logs clear remediation hints on failure. Toggle
    # with LLM_STARTUP_CHECK=false in .env if boot speed matters more
    # than early failure signal (e.g., hot-reload dev loop).
    if settings.llm_startup_check:
        from app.services.llm_service import startup_check
        await startup_check()

    create_db_and_tables()

    # Initialize tool registry
    from app.tools import init_tools
    init_tools()

    # Seed sub-agent templates from JSON files if the DB is empty, then
    # initialise the agent registry from whatever the DB now holds.
    from app.agents.templates import initialize_templates
    initialize_templates()
    from app.agents import init_agents
    init_agents()

    # Bootstrap the KB descriptor if it's missing — the knowledge_search tool
    # reads this file on every bind, so an existing Chroma collection without a
    # descriptor would look empty to the LLM until the next upload.
    from pathlib import Path
    if not Path(settings.kb_descriptor_path).exists():
        try:
            from app.services.rag_service import RAGService
            from app.database import get_chroma_client
            RAGService(get_chroma_client()).rebuild_kb_descriptor()
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "KB descriptor bootstrap failed; knowledge_search will appear empty until an upload.",
                exc_info=True,
            )

    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Logging context middleware.
# SSE chat endpoint is excluded via skip_prefixes — BaseHTTPMiddleware buffers
# streaming responses. The chat router sets its own turn-scoped context.
app.add_middleware(
    LoggingMiddleware,
    service="finchat",
    # POSTs to /api/chat/sessions/<id>/messages are SSE streams; skip the
    # buffering middleware and let the chat router set its own context.
    skip_method_prefixes=[("POST", "/api/chat/sessions/")],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(files.router)
app.include_router(tools.router)
app.include_router(agents_router.router)
app.include_router(widgets_router.router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


@app.post("/api/admin/reset-db")
async def reset_db():
    """Reset the database — drops all tables, recreates them, re-seeds agents.

    WARNING: Destroys all data (sessions, messages, files, agent definitions).
    Use for development only.
    """
    import os
    from pathlib import Path
    from app.database import engine

    # Close all connections
    engine.dispose()

    # Delete DB files
    db_path = Path(settings.database_url.replace("sqlite:///", ""))
    if db_path.exists():
        os.remove(db_path)

    checkpoint_path = db_path.parent / "checkpoints.db"
    if checkpoint_path.exists():
        os.remove(checkpoint_path)

    # Clear in-memory caches
    from app.services.profile_service import _profile_data, _profile_list
    _profile_data.clear()
    _profile_list.clear()
    from app.services.transaction_service import _transaction_data
    _transaction_data.clear()

    # Recreate tables
    create_db_and_tables()

    # Re-seed agents
    from app.agents import init_agents, _AGENTS, _AGENT_NAMES
    _AGENTS.clear()
    _AGENT_NAMES.clear()
    init_agents()

    return {"status": "reset", "message": "Database cleared and re-seeded"}

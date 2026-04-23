from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "AI Agent Chat Platform"
    debug: bool = False

    # Database
    database_url: str = f"sqlite:///{Path(__file__).resolve().parent.parent / 'data' / 'app.db'}"

    # ChromaDB
    chroma_db_path: str = str(Path(__file__).resolve().parent.parent / "data" / "chroma_db")

    # Knowledge base descriptor — generated at upload/delete, injected into knowledge_search.description()
    kb_descriptor_path: str = str(Path(__file__).resolve().parent.parent / "data" / "kb_descriptor.txt")

    # LLM
    openai_api_key: str = ""
    # Optional — point at an OpenAI-compatible gateway / proxy (e.g. a
    # company-hosted LiteLLM, Azure OpenAI, or on-prem inference stack).
    # Leave blank to hit api.openai.com directly. When non-empty it's
    # passed as `base_url` to ChatOpenAI and OpenAIEmbeddings.
    openai_base_url: str = ""
    llm_model: str = "gpt-5"
    sub_agent_llm_model: str = "gpt-4.1"  # Used by the "sub_agent" profile
    embedding_model: str = "text-embedding-3-large"
    max_agent_iterations: int = 15

    # Reasoning effort for gpt-5 / o-family models. Ignored for non-reasoning
    # models. "minimal" | "low" | "medium" | "high". Default "low" — our
    # Planner is mostly routing + light synthesis; deep reasoning is overkill
    # and costs ~5× the latency.
    llm_reasoning_effort: str = "low"

    # Planner prompt revision. Tagged into [turn_summary.v1] so rollback
    # analysis can compare shape distributions across revisions. Changing
    # this value is the rollback mechanism — flip via env var to revert
    # without a redeploy. Bump when the Widget-vs-prose / response-strategy
    # section in enrichment.py changes.
    planner_prompt_revision: str = "v2026-04-22-compound-prose-default"

    # CORS
    cors_origins: list[str] = []

    # LangSmith observability (optional).
    # When langsmith_tracing=True and an api_key is set, every LangGraph
    # invocation — main orchestrator + every sub-agent — is traced to the
    # configured LangSmith project. Endpoint may point at a company
    # self-hosted instance (e.g. https://langsmith.my-company.internal/api/v1)
    # or left blank for the public cloud.
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_endpoint: str = ""         # blank → SDK default (public cloud)
    langsmith_project: str = "finchat"
    langsmith_hide_inputs: bool = False
    langsmith_hide_outputs: bool = False


settings = Settings()


class RAGConfig:
    """Centralized RAG tuning constants. All RAG files import from here."""

    # Indexing
    CHUNK_SIZE: int = 1024      # Section-first: sections get room to breathe
    CHUNK_OVERLAP: int = 100       # Increased from 50 for cross-section bridging
    SMALL_FILE_THRESHOLD: int = 1500  # words — skip chunking below this

    # Retrieval
    SIMILARITY_THRESHOLD: float = 0.3   # Lowered — keyword boost + adaptive threshold handle noise
    CANDIDATE_TOP_K: int = 10           # Candidates from vector search
    FINAL_TOP_K: int = 5                # Results returned to LLM
    KEYWORD_BOOST_FACTOR: float = 0.2   # Term overlap boost weight

    # Adaptive threshold
    ADAPTIVE_TRIGGER: float = 0.8       # Top score must exceed this to trigger
    ADAPTIVE_OFFSET: float = 0.3        # Distance below top score

    # Scoring
    WHOLE_DOC_BOOST: float = 0.05       # Preference for complete documents

    # File-fallback: when >= N chunks from same file match, load full file instead
    FILE_FALLBACK_THRESHOLD: int = 3

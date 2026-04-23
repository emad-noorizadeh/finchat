import logging
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import settings

# Named LLM-variant registry. A "variant" is a named model configuration
# (model + temperature + max_tokens) — distinct from the user profile.
# To add a new variant:
#   1. Add a key + config dict here (model_attr points at the settings field).
#   2. Add the matching field to app/config.py:Settings so operators can override.
#   3. Callers opt in via get_llm("<name>").
_LLM_VARIANTS: dict[str, dict] = {
    "primary": {
        "model_attr": "llm_model",
        "temperature": 0.7,        # ignored for reasoning models (see _is_reasoning_model)
        "max_tokens": 4096,
        # Reasoning effort — consulted only for gpt-5 / o-family models.
        # None means "use the global settings.llm_reasoning_effort".
        "reasoning_effort": None,
    },
    "sub_agent": {
        "model_attr": "sub_agent_llm_model",
        "temperature": 0.3,
        "max_tokens": 2048,
        # Non-reasoning model today; kept for future-proofing.
        "reasoning_effort": None,
    },
}

_VALID_EFFORTS = {"minimal", "low", "medium", "high"}

# Reasoning-family OpenAI models reject `temperature` (only the default of 1
# is accepted) and use `max_completion_tokens` rather than `max_tokens`.
# Pattern covers: o1, o1-mini, o3, o3-mini, o4-mini, gpt-5, gpt-5-*.
# Expand here when OpenAI ships a new family; langchain-openai itself also
# needs to support the name, so pin `langchain-openai` alongside.
_REASONING_MODEL_PATTERN = re.compile(r"^(o[1-9]\b|gpt-5)", re.IGNORECASE)


def _is_reasoning_model(model_name: str) -> bool:
    return bool(_REASONING_MODEL_PATTERN.match(model_name or ""))


_llm_cache: dict[str, BaseChatModel] = {}
_embeddings: Embeddings | None = None


def get_llm(variant: str = "primary") -> BaseChatModel:
    """Get the LangChain chat model for the named variant. Default: "primary".

    Reasoning-family models (o1/o3/o4/gpt-5) don't accept `temperature` — the
    OpenAI API 400s if we send one other than 1. This function detects them
    by name and omits `temperature` from the constructor kwargs. `max_tokens`
    is still passed: langchain-openai translates it to the correct parameter
    (`max_completion_tokens`) for reasoning models.

    Provider is abstracted — callers see only BaseChatModel. Swap ChatOpenAI
    for ChatAnthropic / ChatGoogle etc. by changing this function (or by
    adding a "provider" key to the variant dict when heterogeneity is needed).
    """
    if variant not in _llm_cache:
        if variant not in _LLM_VARIANTS:
            raise ValueError(
                f"Unknown LLM variant: {variant!r}. Known: {sorted(_LLM_VARIANTS)}"
            )
        cfg = _LLM_VARIANTS[variant]
        model_name = getattr(settings, cfg["model_attr"])

        kwargs = {
            "model": model_name,
            "api_key": settings.openai_api_key,
            "max_tokens": cfg["max_tokens"],
        }
        # Optional OpenAI-compatible gateway — only pass base_url when the
        # operator explicitly set it; otherwise langchain-openai picks the
        # default (api.openai.com).
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        # Three-way override: env LLM_IS_REASONING → variant flag → name regex.
        override = (getattr(settings, "llm_is_reasoning", "auto") or "").strip().lower()
        if override == "true":
            is_reasoning = True
        elif override == "false":
            is_reasoning = False
        else:  # "auto" or any unknown value
            is_reasoning = bool(cfg.get("is_reasoning") or _is_reasoning_model(model_name))
        if is_reasoning:
            # Reasoning models only accept temperature=1 (their default).
            # Omit it so langchain-openai doesn't send it on the request.
            effort = cfg.get("reasoning_effort") or getattr(settings, "llm_reasoning_effort", "medium")
            if effort not in _VALID_EFFORTS:
                import logging
                logging.getLogger(__name__).warning(
                    "[llm_reasoning_effort_invalid] got=%r valid=%s — falling back to 'low'",
                    effort, sorted(_VALID_EFFORTS),
                )
                effort = "low"
            kwargs["reasoning_effort"] = effort
        else:
            kwargs["temperature"] = cfg["temperature"]

        _llm_cache[variant] = ChatOpenAI(**kwargs)

        # Visibility on boot — one line per variant, per process.
        import logging
        logging.getLogger(__name__).info(
            "[llm_variant_built] variant=%s model=%s reasoning=%s effort=%s temperature=%s max_tokens=%s",
            variant, model_name, is_reasoning,
            kwargs.get("reasoning_effort", "-"),
            kwargs.get("temperature", "-"),
            kwargs.get("max_tokens"),
        )
    return _llm_cache[variant]


class _ServerTokenizedOpenAIEmbeddings(Embeddings):
    """Thin LangChain Embeddings adapter that calls the OpenAI SDK directly,
    skipping all client-side tokenization.

    Why this exists: langchain-openai's `OpenAIEmbeddings` insists on
    pre-counting tokens client-side for batch sizing. With
    `tiktoken_enabled=True` it downloads BPE files from
    openaipublic.blob.core.windows.net (blocked on most corp networks).
    With `tiktoken_enabled=False` it falls through to
    `transformers.AutoTokenizer` (a 100MB+ optional dep).

    Our indexing pipeline already chunks via character-based
    `RecursiveCharacterTextSplitter`; every payload that reaches embed_*
    is already well under the 8191-token API limit. So we don't need any
    client-side tokenizer — let the API count server-side.

    Used when `settings.openai_embeddings_tiktoken_enabled = False`.
    Compatible with any OpenAI-compatible gateway (LiteLLM, Azure, etc.).
    """

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        from openai import OpenAI
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model
        # Conservative batch size — OpenAI accepts up to 2048 inputs per
        # request, but our typical use is single chunks or small batches.
        self._batch_size = 100

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            resp = self._client.embeddings.create(model=self._model, input=batch)
            out.extend([d.embedding for d in resp.data])
        return out

    def embed_query(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(model=self._model, input=text)
        return resp.data[0].embedding


def get_embeddings() -> Embeddings:
    """Get the singleton LangChain embeddings model.

    Returns an Embeddings instance for RAG, memory, and semantic search.
    Swap to HuggingFaceEmbeddings, GoogleGenerativeAIEmbeddings, etc.
    by changing this single function.
    """
    global _embeddings
    log = logging.getLogger(__name__)
    if _embeddings is None:
        # On airgapped / corp-firewall networks, both tiktoken AND the
        # transformers fallback inside langchain-openai's OpenAIEmbeddings
        # require external resources. We side-step the whole stack and
        # call the OpenAI SDK directly via _ServerTokenizedOpenAIEmbeddings.
        if not settings.openai_embeddings_tiktoken_enabled:
            _embeddings = _ServerTokenizedOpenAIEmbeddings(
                model=settings.embedding_model,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url or None,
            )
            log.info(
                "[embeddings_built] model=%s tokenization=server (raw OpenAI SDK adapter) base_url=%s",
                settings.embedding_model,
                settings.openai_base_url or "default",
            )
        else:
            ekwargs = {
                "model": settings.embedding_model,
                "api_key": settings.openai_api_key,
            }
            if settings.openai_base_url:
                ekwargs["base_url"] = settings.openai_base_url
            _embeddings = OpenAIEmbeddings(**ekwargs)
            log.info(
                "[embeddings_built] model=%s tokenization=client (tiktoken) base_url=%s",
                settings.embedding_model,
                settings.openai_base_url or "default",
            )
    return _embeddings


async def startup_check() -> dict:
    """Sanity-ping every LLM variant + the embeddings model so misconfigured
    API keys / gateway URLs fail LOUDLY at boot instead of on the first user
    turn. Results are logged under [startup_llm_check]; the app never crashes
    on failure — operators see the error in logs and fix it.

    Returns a dict summarising status for anyone who wants to surface it
    (e.g., a `/health` endpoint).
    """
    import asyncio
    import time
    from langchain_core.messages import HumanMessage
    logger = logging.getLogger(__name__)
    report: dict = {"variants": {}, "embedding": {}}

    # --- LLM variants ---
    for variant in _LLM_VARIANTS.keys():
        t0 = time.perf_counter()
        try:
            llm = get_llm(variant)
            resp = await asyncio.wait_for(
                llm.ainvoke([HumanMessage(content="ping")]),
                timeout=30.0,
            )
            content_len = len(resp.content) if isinstance(resp.content, str) else 0
            dt_ms = (time.perf_counter() - t0) * 1000
            report["variants"][variant] = {"ok": True, "duration_ms": round(dt_ms), "content_len": content_len}
            logger.info(
                "[startup_llm_check] variant=%s status=OK duration_ms=%.0f content_len=%d",
                variant, dt_ms, content_len,
            )
        except Exception as e:  # noqa: BLE001
            dt_ms = (time.perf_counter() - t0) * 1000
            report["variants"][variant] = {"ok": False, "duration_ms": round(dt_ms), "error": str(e)}
            logger.error(
                "[startup_llm_check] variant=%s status=FAIL duration_ms=%.0f error=%s "
                "(check OPENAI_API_KEY / OPENAI_BASE_URL / model name)",
                variant, dt_ms, e,
            )

    # --- Embeddings ---
    t0 = time.perf_counter()
    try:
        emb = get_embeddings()
        # OpenAIEmbeddings only exposes sync `embed_query`; run it off-thread
        # to avoid blocking the event loop during startup.
        vec = await asyncio.wait_for(
            asyncio.to_thread(emb.embed_query, "ping"),
            timeout=30.0,
        )
        dt_ms = (time.perf_counter() - t0) * 1000
        report["embedding"] = {"ok": True, "duration_ms": round(dt_ms), "dim": len(vec) if vec else 0}
        logger.info(
            "[startup_llm_check] embedding=%s status=OK duration_ms=%.0f dim=%d",
            settings.embedding_model, dt_ms, len(vec) if vec else 0,
        )
    except Exception as e:  # noqa: BLE001
        dt_ms = (time.perf_counter() - t0) * 1000
        report["embedding"] = {"ok": False, "duration_ms": round(dt_ms), "error": str(e)}
        logger.error(
            "[startup_llm_check] embedding=%s status=FAIL duration_ms=%.0f error=%s "
            "(check OPENAI_API_KEY / OPENAI_BASE_URL / EMBEDDING_MODEL)",
            settings.embedding_model, dt_ms, e,
        )

    return report


def reset():
    """Reset all cached models (useful for tests or config changes)."""
    global _embeddings
    _llm_cache.clear()
    _embeddings = None

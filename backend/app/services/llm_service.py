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
        is_reasoning = cfg.get("is_reasoning") or _is_reasoning_model(model_name)
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


def get_embeddings() -> Embeddings:
    """Get the singleton LangChain embeddings model.

    Returns an Embeddings instance for RAG, memory, and semantic search.
    Swap to HuggingFaceEmbeddings, GoogleGenerativeAIEmbeddings, etc.
    by changing this single function.
    """
    global _embeddings
    if _embeddings is None:
        ekwargs = {
            "model": settings.embedding_model,
            "api_key": settings.openai_api_key,
        }
        # Same optional gateway as ChatOpenAI — only injected when set.
        if settings.openai_base_url:
            ekwargs["base_url"] = settings.openai_base_url
        _embeddings = OpenAIEmbeddings(**ekwargs)
    return _embeddings


def reset():
    """Reset all cached models (useful for tests or config changes)."""
    global _embeddings
    _llm_cache.clear()
    _embeddings = None

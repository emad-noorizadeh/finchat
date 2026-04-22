"""LangSmith-only observability bootstrap.

Call `configure_langsmith()` once at app startup (before any LangChain /
LangGraph code runs). The function translates our Settings fields into the
environment variables the LangChain SDK reads, and **unconditionally
disables** every other telemetry bridge (OpenTelemetry / LS-APM) so the
process cannot silently send data anywhere besides LangSmith.

Design decisions:
  * Settings live in .env via app.config.Settings.
  * Both the canonical `LANGSMITH_*` names and the legacy `LANGCHAIN_*`
    aliases are set, since older library code paths still read the legacy
    names.
  * A missing api_key OR `langsmith_tracing=False` → `LANGSMITH_TRACING=false`.
    The SDK then short-circuits without an endpoint call — zero bytes leave
    the process.
  * `LANGSMITH_OTEL_ENABLED` / `LS_APM_OTEL_ENABLED` / `OTEL_SDK_DISABLED`
    are set explicitly — defence-in-depth if the user / a future release
    tries to flip OTel on implicitly.
  * Logs the effective config (endpoint + project) so operators can confirm
    on boot. Never logs the API key.
"""

from __future__ import annotations

import logging
import os

from app.config import settings

logger = logging.getLogger(__name__)


def _set(name: str, value: str) -> None:
    """Only set the env var if the operator didn't already set it — lets a
    local override in .env win over our defaults."""
    os.environ.setdefault(name, value)


def _force(name: str, value: str) -> None:
    """Always overwrite — used for safety-critical disables."""
    os.environ[name] = value


def configure_langsmith() -> None:
    enabled = bool(settings.langsmith_tracing and settings.langsmith_api_key)

    # Defence-in-depth — always disable OTel / APM so only LangSmith can
    # receive data. Forced (not setdefault) in case a previous run left the
    # env polluted.
    _force("LANGSMITH_OTEL_ENABLED", "false")
    _force("LS_APM_OTEL_ENABLED", "false")
    # The canonical OTel disable switch — picked up by opentelemetry SDK if
    # anything tries to load it.
    _force("OTEL_SDK_DISABLED", "true")

    if not enabled:
        _force("LANGSMITH_TRACING", "false")
        _force("LANGCHAIN_TRACING_V2", "false")
        reason = (
            "no langsmith_api_key" if not settings.langsmith_api_key
            else "langsmith_tracing=false"
        )
        logger.info("[langsmith_disabled] reason=%s", reason)
        return

    # --- Wire the SDK env ---
    _set("LANGSMITH_TRACING", "true")
    _set("LANGCHAIN_TRACING_V2", "true")      # legacy alias

    _set("LANGSMITH_API_KEY", settings.langsmith_api_key)
    _set("LANGCHAIN_API_KEY", settings.langsmith_api_key)

    if settings.langsmith_endpoint:
        _set("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)
        _set("LANGCHAIN_ENDPOINT", settings.langsmith_endpoint)

    _set("LANGSMITH_PROJECT", settings.langsmith_project)
    _set("LANGCHAIN_PROJECT", settings.langsmith_project)

    # Privacy toggles — hide full payloads from spans before upload. Streaming
    # token events are a known gap (GHSA-rr7j-v2q5-chgv) but the env toggles
    # cover the non-streaming paths used throughout this app.
    if settings.langsmith_hide_inputs:
        _set("LANGSMITH_HIDE_INPUTS", "true")
        _set("LANGCHAIN_HIDE_INPUTS", "true")
    if settings.langsmith_hide_outputs:
        _set("LANGSMITH_HIDE_OUTPUTS", "true")
        _set("LANGCHAIN_HIDE_OUTPUTS", "true")

    logger.info(
        "[langsmith_configured] project=%s endpoint=%s hide_inputs=%s hide_outputs=%s",
        settings.langsmith_project,
        settings.langsmith_endpoint or "<public-cloud-default>",
        settings.langsmith_hide_inputs,
        settings.langsmith_hide_outputs,
    )


def trace_config(
    *,
    run_name: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    thread_id: str | None = None,
) -> dict:
    """Build the `config` dict to pass into `graph.ainvoke(..., config=...)`
    or `graph.astream_events(..., config=...)`. Central helper so every
    invocation tags traces consistently.

    If LangSmith is disabled, the config still includes `configurable` +
    `run_name` / `tags` / `metadata` — LangChain ignores the trace-specific
    fields harmlessly when the tracer is off.
    """
    cfg: dict = {
        "run_name": run_name,
        "tags": list(tags or []),
        "metadata": dict(metadata or {}),
    }
    if thread_id is not None:
        cfg["configurable"] = {"thread_id": thread_id}
    return cfg

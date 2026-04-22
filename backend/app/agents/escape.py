"""Escape classifier — runtime guarantee, not a node.

Locked principle #3: every interrupt_node resume runs through this classifier
BEFORE the sub-agent's parse_node sees the utterance. Authors cannot skip or
disable it — it's invoked by the TransferAgentTool driver after reading each
resume value, wiring abort / topic_change signals into `variables._escape_kind`
so the dispatcher's runtime-injected priority-0 edge routes to the escape
response_node.

Phase 1 posture (documented known gap): keyword-only. Users must be explicit
to escape. Mumbled hesitation like "hmm, let me think" is NOT caught and
may be parsed as slot input. The slot's min_confidence threshold bounds the
worst case — low-confidence extractions re-prompt rather than silently
accept a bad value.

Phase 2 upgrade: a narrow LLM classifier for fuzzier phrasings.

All classifications — including `continue` — log at DEBUG with a sha1-prefix
utterance hash so investigations can correlate without raw PII in INFO logs.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


# Regex anchors on word boundaries to avoid matching "canceled" (participle)
# or "stopgap" (substring). "never mind" / "nevermind" both covered.
_ABORT_PATTERNS = [
    re.compile(r"\b(cancel|cancell|abort|stop|quit|exit)\b", re.IGNORECASE),
    re.compile(r"\b(nevermind|never mind)\b", re.IGNORECASE),
    re.compile(r"\bforget (it|that)\b", re.IGNORECASE),
    re.compile(r"\bgo back\b", re.IGNORECASE),
    re.compile(r"\bstart over\b", re.IGNORECASE),
    re.compile(r"\bmain menu\b", re.IGNORECASE),
]

# Topic-change keywords. Phase 1 hand-tuned for FinChat's intent surface.
# Key matches an intent_hint string the orchestrator can route on.
_TOPIC_KEYWORDS: dict[str, list[re.Pattern]] = {
    "check_balance": [
        re.compile(r"\b(balance|how much do i have|what'?s in my)\b", re.IGNORECASE),
    ],
    "show_transactions": [
        re.compile(r"\b(transaction|transactions|recent activity|what did i spend)\b", re.IGNORECASE),
    ],
    "show_profile": [
        re.compile(r"\b(profile|my information|my details)\b", re.IGNORECASE),
    ],
}


@dataclass(frozen=True)
class EscapeResult:
    kind: Literal["continue", "abort", "topic_change"]
    matched: str | None = None
    candidate_intent: str | None = None


def classify(utterance: str) -> EscapeResult:
    """Phase 1 keyword classifier. No LLM. No context-awareness.

    Ordering:
      1. abort patterns — highest priority (user wants out)
      2. topic-change keywords — medium priority (user wants a different flow)
      3. continue — default
    """
    utter_hash = _hash(utterance)

    for pattern in _ABORT_PATTERNS:
        m = pattern.search(utterance)
        if m:
            result = EscapeResult(kind="abort", matched=m.group(0))
            _log(result, utter_hash)
            return result

    for intent, patterns in _TOPIC_KEYWORDS.items():
        for pattern in patterns:
            m = pattern.search(utterance)
            if m:
                result = EscapeResult(
                    kind="topic_change",
                    matched=m.group(0),
                    candidate_intent=intent,
                )
                _log(result, utter_hash)
                return result

    result = EscapeResult(kind="continue")
    _log(result, utter_hash)
    return result


def _hash(utterance: str) -> str:
    return hashlib.sha1(utterance.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _log(result: EscapeResult, utter_hash: str) -> None:
    logger.debug(
        "[subagent_escape.v1] kind=%s matched=%r candidate_intent=%r utterance_hash=%s",
        result.kind, result.matched, result.candidate_intent, utter_hash,
    )

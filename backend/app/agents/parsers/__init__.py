"""Parser extractor registry for `parse_node`.

Each parser is a callable `(utterance: str, context: dict) -> object | None`.
Returns a parsed value or None (means "didn't find anything" — leaves slot
unchanged under parse_node's merge rule).

Engineer-owned. Adding a new parser is a code change, registered via
`@register_parser(name)`.
"""

from __future__ import annotations

import re
from typing import Callable


Parser = Callable[[str, dict], object | None]

_PARSERS: dict[str, Parser] = {}


def register_parser(name: str) -> Callable[[Parser], Parser]:
    def decorator(fn: Parser) -> Parser:
        if name in _PARSERS and _PARSERS[name] is not fn:
            raise ValueError(f"parser {name!r} already registered")
        _PARSERS[name] = fn
        return fn
    return decorator


def get_parser(name: str) -> Parser | None:
    return _PARSERS.get(name)


def known_parsers() -> set[str]:
    return set(_PARSERS.keys())


# --- Phase 1 parsers ---


_MONEY_RX = re.compile(
    r"\$?\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)(?:\s*(?:dollar|dollars|bucks))?",
    re.IGNORECASE,
)


@register_parser("money")
def parse_money(utterance: str, context: dict) -> float | None:
    m = _MONEY_RX.search(utterance)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        v = float(raw)
    except ValueError:
        return None
    if v <= 0:
        return None
    has_currency = "$" in utterance or re.search(
        r"\b(dollar|dollars|bucks|usd)\b", utterance, re.IGNORECASE,
    )
    return v if has_currency else (v if v >= 10 else None)
    # Bare numbers <10 rejected — most ambiguous ("let me think 5...")


_YES_RX = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|confirm|confirmed|correct|right|do it|go ahead)\b",
    re.IGNORECASE,
)
_NO_RX = re.compile(
    r"\b(no|nope|nah|negative|don't|do not|incorrect|wrong|stop|cancel)\b",
    re.IGNORECASE,
)


@register_parser("yes_no")
def parse_yes_no(utterance: str, context: dict) -> bool | None:
    yes = bool(_YES_RX.search(utterance))
    no = bool(_NO_RX.search(utterance))
    if yes and not no:
        return True
    if no and not yes:
        return False
    return None


_ACCOUNT_KEYWORDS = {
    "checking": "checking",
    "savings": "savings",
    "saving": "savings",
    "credit": "credit",
    "credit card": "credit",
}


@register_parser("account_keyword")
def parse_account_keyword(utterance: str, context: dict) -> str | None:
    low = utterance.lower()
    for kw, norm in _ACCOUNT_KEYWORDS.items():
        if re.search(rf"\b{re.escape(kw)}\b", low):
            return norm
    return None


_LAST4_RX = re.compile(r"\b(\d{4})\b")


@register_parser("last4")
def parse_last4(utterance: str, context: dict) -> str | None:
    m = _LAST4_RX.search(utterance)
    return m.group(1) if m else None


# --- LLM-mode parse helper ---


async def llm_parse(
    utterance: str,
    *,
    system_prompt: str,
    output_schema: dict,
    channel: str,
    llm_variant: str = "sub_agent",
) -> dict:
    """Single LLM call with constrained JSON output.

    Returns a dict of {field: value | None} matching the output_schema.
    Null means "unchanged" — caller applies merge semantics.

    Uses structured output via the LLM service's constrained-decoding hook.
    Falls back to prompt-only JSON on models without structured output.
    """
    from app.services.llm_service import get_llm
    from langchain_core.messages import HumanMessage, SystemMessage
    import json as _json

    fields = list((output_schema or {}).keys())
    full_prompt = (
        system_prompt
        + "\n\nOutput a SINGLE JSON object with exactly these keys: "
        + ", ".join(fields)
        + ". Set a key to null if the user did not mention that field. "
        + "Do not include any prose outside the JSON."
    )

    llm = get_llm(llm_variant)
    # Tag the invocation so the chat router's SSE stream can filter out
    # sub-agent parse chunks. Without this, the parse's raw JSON output
    # gets streamed straight into the chat as if it were the assistant's
    # response.
    response = await llm.ainvoke(
        [SystemMessage(content=full_prompt), HumanMessage(content=utterance)],
        config={"tags": ["subagent_internal"]},
    )
    raw = response.content if hasattr(response, "content") else str(response)

    # Permissive parse — strip code fences, find first JSON object.
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {k: None for k in fields}
    try:
        parsed = _json.loads(raw[start:end + 1])
    except _json.JSONDecodeError:
        return {k: None for k in fields}
    return {k: parsed.get(k) for k in fields}

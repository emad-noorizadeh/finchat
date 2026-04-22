"""Pure functions that extract concise LLM-friendly text from widget data.

One summarizer per widget type. Used by ToolResult to auto-derive to_llm
from widget data — tool authors only build one output (the widget).
"""

import json


def summarize_transaction_list(data: dict) -> str:
    shape = data.get("shape")

    if shape == "groups":
        groups = data.get("groups", []) or []
        if not groups:
            return "No transactions to group."
        group_by = data.get("group_by", "category")
        top3 = ", ".join(
            f"{g.get('group','')} {g.get('total_amount_display', '')}"
            for g in groups[:3]
        )
        return f"{len(groups)} {group_by} group(s). Top: {top3}"

    txns = data.get("transactions", []) or []
    summary = data.get("summary") or {}
    total = data.get("total", len(txns))
    if not txns:
        return "No transactions found."
    top3 = ", ".join(f"{t.get('description','')} {t.get('amount','')}" for t in txns[:3])
    net = summary.get("net_display")
    suffix = f" Net: {net}." if net else ""
    return f"{len(txns)} of {total} transactions.{suffix} Latest: {top3}"


def summarize_account_summary(data: dict) -> str:
    accts = data.get("accounts", [])
    if not accts:
        return "No accounts found."
    parts = []
    for a in accts:
        balance = a.get("balance", 0)
        parts.append(f"{a.get('display_name', '')} (${balance:,.2f})")
    return f"{len(accts)} accounts: {', '.join(parts)}"


def summarize_profile_card(data: dict) -> str:
    name = data.get("name", "Unknown")
    city = data.get("city", "")
    tier = data.get("rewards_tier", "")
    score = ""
    scores = data.get("credit_scores", [])
    if scores:
        score = f", credit score {scores[0].get('score', '')}"
    return f"Profile: {name}, {city}, {tier}{score}"


def summarize_transfer_confirmation(data: dict) -> str:
    cid = data.get("confirmation_id", "")
    amount = data.get("amount", 0)
    src = data.get("from", "")
    dst = data.get("to", "")
    status = data.get("status", "")
    return f"Transfer {status}: {cid}, ${amount:,.2f} from {src} to {dst}"


def summarize_confirmation_request(data: dict) -> str:
    details = data.get("details", "")
    fields = data.get("fields", [])
    if fields:
        field_text = ", ".join(f"{f.get('label','')}: {f.get('value','')}" for f in fields)
        return f"Confirmation requested: {field_text}"
    return f"Confirmation requested: {details}"


def summarize_text_card(data: dict) -> str:
    return data.get("content", "")[:300]


_SUMMARIZERS = {
    "transaction_list": summarize_transaction_list,
    "account_summary": summarize_account_summary,
    "profile_card": summarize_profile_card,
    "transfer_confirmation": summarize_transfer_confirmation,
    "confirmation_request": summarize_confirmation_request,
    "text_card": summarize_text_card,
}


def widget_to_llm(widget: dict) -> str:
    """Convert a widget response to concise LLM-friendly text.

    Precedence:
      1. Catalog entry's voice_summary_template (resolves {{var}} against widget data).
      2. Hand-written summarizer in _SUMMARIZERS.
      3. Deterministic generic fallback based on data shape.
    """
    widget_type = widget.get("widget", "")
    data = widget.get("data", {})

    # Precedence 1: catalog template (set by designers per widget)
    try:
        from app.widgets.catalog import get_catalog_entry

        entry = get_catalog_entry(widget_type)
        if entry and entry.get("voice_summary_template"):
            return _resolve_voice_template(entry["voice_summary_template"], data)
    except Exception:
        pass  # fall through

    # Precedence 2: hand-written summarizer
    summarizer = _SUMMARIZERS.get(widget_type)
    if summarizer:
        return summarizer(data)

    # Precedence 3: deterministic generic fallback
    return _generic_summary(widget_type, data)


def _resolve_voice_template(template: str, data) -> str:
    """Replace {{var}} and {{nested.path}} placeholders with values from data.

    For list data with a single `{{N}}` style placeholder referencing the list itself,
    substitute the length. For scalars, stringify.
    """
    import re

    def replacer(match):
        key = match.group(1).strip()
        value = _lookup(data, key)
        if isinstance(value, list):
            return str(len(value))
        if value is None:
            return ""
        return str(value)

    return re.sub(r"\{\{(.+?)\}\}", replacer, template)


def _lookup(data, key: str):
    """Walk a dotted key into a dict/list structure. Supports top-level key for lists (e.g., 'transactions' → whole list)."""
    if "." in key:
        parts = key.split(".")
        cursor = data
        for p in parts:
            if isinstance(cursor, dict):
                cursor = cursor.get(p)
            else:
                return None
        return cursor
    # Single key
    if isinstance(data, dict):
        # Special convention: if the template key matches the wrapping data's
        # only list-valued key, return that list (for length-substitution).
        if key in data:
            return data[key]
        # Also handle the case where `data` IS the field (e.g., account_summary's
        # {"accounts": [...]} — template uses {{accounts}})
        return None
    if isinstance(data, list) and key in ("items", "_", "list"):
        return data
    return None


def _generic_summary(widget_type: str, data) -> str:
    """Deterministic TTS-safe summary for any widget without a dedicated path.

    - dict data → "A <widget_type> with N fields: k1=v1, k2=v2"  (keys capped at 5, values truncated to 40 chars, falsy skipped)
    - list data → "A <widget_type> listing N item(s)"
    - other     → "A <widget_type>"
    """
    name = widget_type.replace("_", " ") or "widget"

    if isinstance(data, dict):
        parts: list[str] = []
        for k, v in data.items():
            if not v:
                continue
            if len(parts) >= 5:
                break
            s = str(v)
            if len(s) > 40:
                s = s[:37] + "…"
            parts.append(f"{k}={s}")
        if not parts:
            return f"A {name}"
        return f"A {name} with {len(parts)} field(s): " + ", ".join(parts)

    if isinstance(data, list):
        return f"A {name} listing {len(data)} item(s)"

    return f"A {name}"

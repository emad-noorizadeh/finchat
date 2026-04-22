"""Builder functions for each widget type. Tools use these to create consistent widget responses."""

import json


_DEFAULT_TRANSACTIONS_TITLE = "Recent Transactions"


def _scope_title_from_filters(applied: dict) -> str:
    """Derive a scope-aware title like "Fees" or 'Matching "coffee"' from
    applied_filters. Returns "" to mean "keep the default title".

    Callers only override the default title when this returns non-empty, so
    an explicit title from the caller always wins (falsy override).
    """
    if not applied:
        return ""
    # Prefer the most specific scope signal first.
    cat = applied.get("category")
    if cat:
        pretty = _pretty_category(cat)
        # Single-filter category case gets the cleanest title.
        if len(applied) == 1:
            return pretty
        # With extra filters, still name the category so the user sees the
        # primary scope; extra chips in the widget carry the rest.
        return pretty
    q = applied.get("query")
    if q and len(applied) == 1:
        return f'Transactions matching "{q}"'
    direction = applied.get("direction")
    if direction and len(applied) == 1:
        return "Credits" if direction == "credit" else "Debits"
    if applied.get("account") and len(applied) == 1:
        return f"Transactions on {applied['account']}"
    return "Filtered transactions"


def _pretty_category(cat: str) -> str:
    """Short human label for a displayCategory constant.

    PURCHASE_GROCERY → "Groceries"
    FEE_MONTHLY     → "Fees"
    PAYROLL_DIRECT  → "Payroll"
    TRANSFER_WIRE   → "Transfers"
    """
    if not cat:
        return ""
    # Family-level labels — drop the specific subtype so "FEE_MONTHLY" and
    # "FEE_ATM" both read as "Fees" at the header.
    family_labels = {
        "FEE": "Fees",
        "FEES": "Fees",
        "PURCHASE": "Purchases",
        "PAYMENT": "Payments",
        "PAYROLL": "Payroll",
        "TRANSFER": "Transfers",
        "DEPOSIT": "Deposits",
        "REFUND": "Refunds",
        "REWARDS": "Rewards",
        "INTEREST": "Interest",
        "ATM": "ATM",
        "CHECK": "Checks",
    }
    head, _, tail = cat.partition("_")
    if head in family_labels:
        # Specific families where the subtype is the interesting part.
        if head == "PURCHASE" and tail:
            # GROCERY → Groceries, GAS → Gas, etc.
            sub = tail.replace("_", " ").lower()
            sub_pretty = {"grocery": "Groceries", "gas": "Gas", "food": "Food",
                          "retail": "Retail", "online": "Online",
                          "electronics": "Electronics", "travel": "Travel",
                          "transport": "Transport",
                          "subscription": "Subscriptions"}
            return sub_pretty.get(sub, sub.title())
        return family_labels[head]
    return cat.replace("_", " ").title()


def transaction_list_widget(
    payload,
    page: int = 1,
    page_size: int = 10,
    title: str = _DEFAULT_TRANSACTIONS_TITLE,
) -> str:
    """Build a transaction_list widget.

    Accepts either:
      - a plain list[dict] — legacy shape, rendered as a flat list
      - an analyzer result dict with "shape": "flat" | "groups"

    The frontend TransactionList component dispatches on `data.shape`.
    When the analyzer applied filters (category/query/date range/etc.), the
    builder derives a scope-aware title and passes applied_filters through
    so the widget can render a visible scope banner — the user sees "Fees"
    instead of "Recent Transactions" and understands the widget holds only
    the pre-filtered subset.
    """
    data: dict
    metadata: dict
    total: int
    applied: dict = {}
    if isinstance(payload, dict):
        applied = payload.get("applied_filters") or {}

    # Only auto-title when the caller accepted the default — an explicit title
    # from the render tool / Presenter always wins.
    if title == _DEFAULT_TRANSACTIONS_TITLE:
        scope_title = _scope_title_from_filters(applied)
        if scope_title:
            title = scope_title

    if isinstance(payload, dict) and payload.get("shape") == "groups":
        groups = payload.get("groups") or []
        total = payload.get("total", 0)
        data = {
            "shape": "groups",
            "groups": groups,
            "group_by": payload.get("group_by", ""),
            "summary": payload.get("summary") or {},
            "applied_filters": applied,
            "page": page,
            "page_size": page_size,
        }
        metadata = {
            "total": total,
            "group_count": len(groups),
            "has_more": False,
        }
    elif isinstance(payload, dict) and payload.get("shape") == "flat":
        transactions = payload.get("transactions") or []
        total = payload.get("total", len(transactions))
        showing = len(transactions)
        data = {
            "shape": "flat",
            "transactions": transactions,
            "summary": payload.get("summary") or {},
            "applied_filters": applied,
            "page": page,
            "page_size": page_size,
        }
        metadata = {"total": total, "showing": showing, "has_more": total > showing}
    else:
        # Legacy: plain list of transaction rows.
        transactions = payload if isinstance(payload, list) else []
        total = len(transactions)
        showing = total
        data = {
            "shape": "flat",
            "transactions": transactions,
            "page": page,
            "page_size": page_size,
        }
        metadata = {"total": total, "showing": showing, "has_more": False}

    return json.dumps({
        "widget": "transaction_list",
        "title": title,
        "icon": "list",
        "data": data,
        "actions": [],
        "metadata": metadata,
    })


def account_summary_widget(accounts: list, title: str = "Your Accounts") -> str:
    return json.dumps({
        "widget": "account_summary",
        "title": title,
        "icon": "credit-card",
        "data": {"accounts": accounts},
        "actions": [],
        "metadata": {"total": len(accounts)},
    })


def profile_card_widget(profile_data: dict, title: str = "Your Profile") -> str:
    return json.dumps({
        "widget": "profile_card",
        "title": title,
        "icon": "user",
        "data": profile_data,
        "actions": [],
        "metadata": {},
    })


def transfer_confirmation_widget(
    from_account: str, to_account: str, amount: float,
    date: str, confirmation_id: str, status: str = "COMPLETED",
) -> str:
    return json.dumps({
        "widget": "transfer_confirmation",
        "title": "Transfer Complete" if status == "COMPLETED" else "Transfer Status",
        "icon": "check-circle",
        "data": {
            "from": from_account,
            "to": to_account,
            "amount": amount,
            "date": date,
            "confirmation_id": confirmation_id,
            "status": status,
        },
        "actions": [
            {"id": "dismiss", "label": "Done", "style": "primary", "type": "dismiss"}
        ],
        "metadata": {"status": status},
    })


def confirmation_request_widget(title: str, details: str, fields: list[dict] = None) -> str:
    return json.dumps({
        "widget": "confirmation_request",
        "title": title,
        "icon": "alert-circle",
        "data": {
            "details": details,
            "fields": fields or [],
        },
        "actions": [
            {"id": "confirm", "label": "Confirm", "style": "primary", "type": "resume"},
            {"id": "cancel", "label": "Cancel", "style": "danger", "type": "resume"},
        ],
        "metadata": {},
    })


def text_card_widget(content: str, title: str = "") -> str:
    return json.dumps({
        "widget": "text_card",
        "title": title,
        "data": {"content": content},
        "actions": [
            {"id": "dismiss", "label": "OK", "style": "secondary", "type": "dismiss"}
        ],
        "metadata": {},
    })


def profile_with_accounts_widget(profile: dict, accounts: list, title: str = "") -> str:
    """Designed composite — profile header + accounts list in one card.

    Rule-1 target for the (profile_data, accounts_data) slot combination.
    Builder is pure: receives pre-resolved slot data, no catalog lookups.
    """
    return json.dumps({
        "widget": "profile_with_accounts",
        "title": title,
        "icon": "user-check",
        "data": {"profile": profile, "accounts": accounts},
        "actions": [],
        "metadata": {"account_count": len(accounts) if isinstance(accounts, list) else 0},
    })


def transfer_form_widget(
    amount: float | None = None,
    from_account: dict | None = None,
    to_account: dict | None = None,
    source_options: list | None = None,
    target_options: list | None = None,
    validation_id: str | None = None,
    title: str = "Confirm transfer",
) -> str:
    """Interactive transfer form. User picks final account + amount and clicks
    Transfer; widget action handler calls transfer_money(submit). Used by
    Transfer chat template's response_node(return_mode=widget)."""
    return json.dumps({
        "widget": "transfer_form",
        "title": title,
        "icon": "send",
        "data": {
            "amount": amount,
            "from_account": from_account,
            "to_account": to_account,
            "source_options": source_options or [],
            "target_options": target_options or [],
            "validation_id": validation_id,
        },
        "actions": [
            {"id": "submit", "label": "Transfer", "style": "primary"},
            {"id": "cancel", "label": "Cancel", "style": "secondary"},
        ],
        "metadata": {"status": "pending"},
    })


def refund_form_widget(
    account_details: dict | None = None,
    refundable_transactions: list | None = None,
    total_amount: float | None = None,
    title: str = "Request a fee refund",
) -> str:
    """Interactive refund form. User sees all eligible fees, picks one, and
    clicks Request refund; the widget-action handler calls
    RefundService.submit_refund. Used by the Fee Refund chat template's
    response_node(return_mode=widget)."""
    return json.dumps({
        "widget": "refund_form",
        "title": title,
        "icon": "receipt-refund",
        "data": {
            "account_details": account_details or {},
            "refundable_transactions": refundable_transactions or [],
            "total_amount": total_amount or 0.0,
            "selected_activity_reference": None,
            "decision": None,
        },
        "actions": [
            {"id": "select", "label": "Continue", "style": "primary"},
            {"id": "submit", "label": "Request refund", "style": "primary"},
            {"id": "back", "label": "Back", "style": "secondary"},
            {"id": "cancel", "label": "Cancel", "style": "secondary"},
        ],
        "metadata": {"status": "pending"},
    })


def generic_composite_widget(sections: list, title: str = "") -> str:
    """Generic vertical stack composite.

    Each section is a {widget_type, data} descriptor; the frontend's
    WidgetRenderer handles per-section rendering in mode='composite'.
    Builder stays pure — no catalog access, no per-section rendering.
    """
    return json.dumps({
        "widget": "generic_composite",
        "title": title,
        "icon": "layers",
        "data": {"sections": sections},
        "actions": [],
        "metadata": {"section_count": len(sections) if isinstance(sections, list) else 0},
    })

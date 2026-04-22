"""Transaction analyzer — single place where we filter, group, sort, and summarize.

Design:
- Pure Python over the canonical flat list of activityRecords (no pandas — 42
  records per user doesn't justify the dependency).
- All views on get_transactions_data route through `analyze(records, query)` so
  there's one code path.
- Output shape is either a flat list of rows OR a list of groups (each group
  containing its own rows + total/count). The widget renders both shapes.

Row shape is stable — same keys as app.services.transaction_service._format_transaction:
    reference, description, amount, direction, date, status, category, account
Amount is always a display string like "$78.54". `amount_value` is the parsed
float, added so the widget can sort client-side without re-parsing.
"""

from dataclasses import dataclass, field
from datetime import datetime


# Canonical category taxonomy — from the displayCategory values seen in the
# mock data. Purpose is only to expose a deterministic list to callers; the
# analyzer accepts any string.
KNOWN_CATEGORIES = [
    "PURCHASE_GAS", "PURCHASE_GROCERY", "PURCHASE_FOOD", "PURCHASE_RETAIL",
    "PURCHASE_ONLINE", "PURCHASE_ELECTRONICS", "PURCHASE_TRAVEL",
    "PURCHASE_TRANSPORT", "PURCHASE_SUBSCRIPTION",
    "PAYMENT_BILL", "PAYMENT_RENT", "PAYMENT_CREDIT_CARD",
    "PAYROLL_DIRECT", "INTEREST_CREDIT", "DEPOSIT_CHECK",
    "ATM_WITHDRAWAL", "CHECK_PAID",
    "TRANSFER_INTERNAL", "TRANSFER_WIRE", "TRANSFER_ZELLE",
    "FEE_ATM", "FEE_INTEREST", "FEE_MONTHLY", "FEE_WIRE",
    "FEE_REBATE_ATM", "FEE_WAIVER",
    "REFUND_MERCHANT", "REWARDS_REDEMPTION",
]


@dataclass
class TxnQuery:
    """A query against the transaction stream. All fields optional."""
    # Filters
    query: str = ""                    # substring over description|category|account|merchant
    category: str = ""                 # exact displayCategory
    direction: str = ""                # "credit" | "debit"
    account: str = ""                  # substring on accountLabel
    date_from: str = ""                # "MM/DD/YYYY" or "YYYY-MM-DD"
    date_to: str = ""                  # ditto
    min_amount: float | None = None    # compare against absolute amount
    max_amount: float | None = None
    # Shape
    group_by: str = ""                 # "category" | "merchant" | "date" | "account"
    sort: str = "date_desc"            # "date_desc" | "date_asc" | "amount_desc" | "amount_asc"
    limit: int | None = None


# ---------- normalization ----------


def _parse_amount(s) -> float:
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _row(txn: dict) -> dict:
    """Normalize one activityRecord into the stable row shape.

    Date precedence: settlementDate → originDate. Pending transactions have no
    settlementDate (they haven't cleared), so fall back to originDate (the
    authorization date). Keeps pending rows dated and sortable instead of
    sinking to the bottom with null dates.
    """
    amount_str = txn.get("transactionAmount", "")
    settled = txn.get("settlementDate") or ""
    origin = txn.get("originDate") or ""
    date = settled or origin
    return {
        "reference": txn.get("activityReference", ""),
        "description": txn.get("primaryDescription", ""),
        "amount": amount_str,
        "amount_value": _parse_amount(amount_str),
        "direction": txn.get("entryDirection", ""),
        "date": date,
        "status": txn.get("processingState", ""),
        "category": txn.get("displayCategory", ""),
        "account": txn.get("linkedAccount", {}).get("accountLabel", ""),
        "merchant": _derive_merchant(txn),
        "status_description": txn.get("statusDescription", ""),
    }


def _derive_merchant(txn: dict) -> str:
    """Best-effort merchant from description — '<MERCHANT> - <rest>' or first 3 words."""
    desc = txn.get("primaryDescription", "") or ""
    if " - " in desc:
        return desc.split(" - ", 1)[0].strip()
    parts = desc.split()
    return " ".join(parts[:3]) if parts else ""


# ---------- filters ----------


def _matches(row: dict, q: TxnQuery) -> bool:
    if q.query:
        needle = q.query.lower()
        haystack = " ".join([
            row["description"], row["category"], row["account"], row["merchant"],
        ]).lower()
        if needle not in haystack:
            return False
    if q.category and row["category"] != q.category:
        return False
    if q.direction and row["direction"] != q.direction:
        return False
    if q.account:
        if q.account.lower() not in row["account"].lower():
            return False
    if q.min_amount is not None and row["amount_value"] < q.min_amount:
        return False
    if q.max_amount is not None and row["amount_value"] > q.max_amount:
        return False
    if q.date_from or q.date_to:
        d = _parse_date(row["date"])
        if d is None:
            return False
        if q.date_from:
            df = _parse_date(q.date_from)
            if df and d < df:
                return False
        if q.date_to:
            dt = _parse_date(q.date_to)
            if dt and d > dt:
                return False
    return True


# ---------- sort / group ----------


def _sort_rows(rows: list[dict], sort_key: str) -> list[dict]:
    if sort_key == "date_asc":
        return sorted(rows, key=lambda r: _parse_date(r["date"]) or datetime.min)
    if sort_key == "amount_desc":
        return sorted(rows, key=lambda r: r["amount_value"], reverse=True)
    if sort_key == "amount_asc":
        return sorted(rows, key=lambda r: r["amount_value"])
    # default date_desc
    return sorted(rows, key=lambda r: _parse_date(r["date"]) or datetime.min, reverse=True)


_GROUP_KEY = {
    "category": "category",
    "merchant": "merchant",
    "date": "date",
    "account": "account",
}


def _group_rows(rows: list[dict], group_by: str) -> list[dict]:
    key_name = _GROUP_KEY.get(group_by)
    if not key_name:
        raise ValueError(f"Unknown group_by: {group_by!r}")
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        bucket = r.get(key_name) or "Uncategorized"
        buckets.setdefault(bucket, []).append(r)

    groups = []
    for name, items in buckets.items():
        total = sum(r["amount_value"] for r in items)
        groups.append({
            "group": name,
            "count": len(items),
            "total_amount": round(total, 2),
            "total_amount_display": f"${total:,.2f}",
            "transactions": items,
        })
    # Sort groups by total descending by default — most-spent-in first.
    groups.sort(key=lambda g: g["total_amount"], reverse=True)
    return groups


# ---------- public api ----------


def analyze(records: list[dict], query: TxnQuery) -> dict:
    """Run filter / group / sort / limit. Returns a shape suitable for the widget.

    Returns:
        {"shape": "flat", "transactions": [...], "total": int, "summary": {...}, "applied_filters": {...}}
      OR
        {"shape": "groups", "groups": [...], "total": int, "summary": {...}, "applied_filters": {...}}

    summary always contains: count, total_inflow, total_outflow, net.
    applied_filters preserves the non-default TxnQuery params so the widget can
    display the scope of what it's showing — "Fees · 2026-03-01 – 2026-04-15" —
    instead of a generic "Recent Transactions" heading that misleads the user
    into thinking the widget holds the full transaction set.
    """
    rows = [_row(r) for r in records]
    rows = [r for r in rows if _matches(r, query)]
    rows = _sort_rows(rows, query.sort)

    summary = _summarize(rows)
    applied = _applied_filters(query)

    if query.group_by:
        groups = _group_rows(rows, query.group_by)
        # Apply limit at the group level (most-spent top-N).
        if query.limit:
            groups = groups[: query.limit]
        return {
            "shape": "groups",
            "groups": groups,
            "total": len(rows),
            "summary": summary,
            "group_by": query.group_by,
            "applied_filters": applied,
        }

    if query.limit:
        rows = rows[: query.limit]

    return {
        "shape": "flat",
        "transactions": rows,
        "total": len(rows),
        "summary": summary,
        "applied_filters": applied,
    }


def _applied_filters(query: TxnQuery) -> dict:
    """Serialize non-default TxnQuery fields. Keys absent when the filter is a no-op."""
    out: dict = {}
    if query.query:
        out["query"] = query.query
    if query.category:
        out["category"] = query.category
    if query.direction:
        out["direction"] = query.direction
    if query.account:
        out["account"] = query.account
    if query.date_from:
        out["date_from"] = query.date_from
    if query.date_to:
        out["date_to"] = query.date_to
    if query.min_amount is not None:
        out["min_amount"] = query.min_amount
    if query.max_amount is not None:
        out["max_amount"] = query.max_amount
    return out


def _summarize(rows: list[dict]) -> dict:
    inflow = sum(r["amount_value"] for r in rows if r["direction"] == "credit")
    outflow = sum(r["amount_value"] for r in rows if r["direction"] == "debit")
    return {
        "count": len(rows),
        "total_inflow": round(inflow, 2),
        "total_outflow": round(outflow, 2),
        "net": round(inflow - outflow, 2),
        "total_inflow_display": f"${inflow:,.2f}",
        "total_outflow_display": f"${outflow:,.2f}",
        "net_display": f"${(inflow - outflow):,.2f}",
    }

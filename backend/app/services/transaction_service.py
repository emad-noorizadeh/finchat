import json
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "profile"

# In-memory store: keyed by customerLoginId
_transaction_data: dict[str, dict] = {}


def load_transactions(login_id: str, file_prefix: str):
    """Load all transaction files for a user into memory."""
    _transaction_data[login_id] = {
        "transactions": _load_json(PROFILE_DIR / f"{file_prefix}_transactions.json"),
        "by_date": _load_json(PROFILE_DIR / f"{file_prefix}_transactions_by_date.json"),
        "by_merchant": _load_json(PROFILE_DIR / f"{file_prefix}_transactions_by_merchant.json"),
    }


def get_transactions(login_id: str) -> dict:
    """Get the raw transactions for a user."""
    data = _transaction_data.get(login_id)
    if not data:
        return {}
    return data["transactions"]


def get_transaction_records(login_id: str) -> list[dict]:
    """Get the flat list of activity records."""
    txn_data = get_transactions(login_id)
    return txn_data.get("responseData", {}).get("activityRecords", [])


def get_transactions_by_date(login_id: str) -> dict:
    """Get transactions grouped by date."""
    data = _transaction_data.get(login_id)
    if not data:
        return {}
    return data["by_date"]


def get_transactions_by_merchant(login_id: str) -> dict:
    """Get transactions grouped by merchant."""
    data = _transaction_data.get(login_id)
    if not data:
        return {}
    return data["by_merchant"]


def search_transactions(login_id: str, query: str, limit: int = 20) -> list[dict]:
    """Search transactions by keyword across description, category, and account."""
    records = get_transaction_records(login_id)
    query_lower = query.lower()

    results = []
    for txn in records:
        desc = txn.get("primaryDescription", "").lower()
        category = txn.get("displayCategory", "").lower()
        account = txn.get("linkedAccount", {}).get("accountLabel", "").lower()
        if query_lower in desc or query_lower in category or query_lower in account:
            results.append(_format_transaction(txn))

    return results[:limit]


def get_recent_transactions(login_id: str, limit: int = 10, account_filter: str = "") -> list[dict]:
    """Get the most recent transactions, optionally filtered by account."""
    records = get_transaction_records(login_id)

    if account_filter:
        account_lower = account_filter.lower()
        records = [
            t for t in records
            if account_lower in t.get("linkedAccount", {}).get("accountLabel", "").lower()
        ]

    return [_format_transaction(t) for t in records[:limit]]


def is_loaded(login_id: str) -> bool:
    """Check if transactions are loaded in memory."""
    return login_id in _transaction_data


def _format_transaction(txn: dict) -> dict:
    """Format a transaction record for API/tool output."""
    return {
        "reference": txn.get("activityReference", ""),
        "description": txn.get("primaryDescription", ""),
        "amount": txn.get("transactionAmount", ""),
        "direction": txn.get("entryDirection", ""),
        "date": txn.get("settlementDate", ""),
        "status": txn.get("processingState", ""),
        "category": txn.get("displayCategory", ""),
        "account": txn.get("linkedAccount", {}).get("accountLabel", ""),
    }


def _load_json(path: Path) -> dict | list:
    """Load a JSON file, return empty dict if not found."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

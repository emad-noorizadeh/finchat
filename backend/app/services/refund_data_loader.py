"""Refund mock-data loader. Mirrors transfer_data_loader.

Layout: api_data/refund/<login_id>/fee_refund.json
  {
    "fee_transactions": {...},
    "refund_approved":  {...},
    "refund_denied":    {...}
  }

A missing user directory means the customer has no fee-refund data — the
service layer will treat this as "no refundable fees" (Chris's case).
"""

import json
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent.parent.parent / "api_data" / "refund"


class RefundDataLoader:
    def __init__(self, base_path: str | Path | None = None):
        self._base_path = Path(base_path) if base_path else BASE_PATH
        self._cache: dict[str, dict] = {}

    def _load_file(self, login_id: str) -> dict | None:
        """Load and cache the refund JSON for a user. Returns None if the
        user has no refund data on disk (== no refundable fees, not an error).
        """
        if login_id in self._cache:
            return self._cache[login_id]

        file_path = self._base_path / login_id / "fee_refund.json"
        if not file_path.exists():
            self._cache[login_id] = None
            return None

        data = json.loads(file_path.read_text(encoding="utf-8"))
        self._cache[login_id] = data
        return data

    def get_step_data(self, login_id: str, step_key: str) -> dict | None:
        """Return data for a specific refund step ("fee_transactions",
        "refund_approved", "refund_denied"). Returns None if the user has
        no data OR the step key is missing.
        """
        data = self._load_file(login_id)
        if data is None:
            return None
        return data.get(step_key)

    def user_has_data(self, login_id: str) -> bool:
        return self._load_file(login_id) is not None

    def get_available_users(self) -> list[str]:
        if not self._base_path.exists():
            return []
        return sorted([
            d.name for d in self._base_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

    def clear_cache(self):
        self._cache.clear()


_loader: RefundDataLoader | None = None


def get_refund_data_loader() -> RefundDataLoader:
    """Module-level singleton — mirrors the transfer loader pattern."""
    global _loader
    if _loader is None:
        _loader = RefundDataLoader()
    return _loader

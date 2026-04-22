import json
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent.parent.parent / "api_data" / "transfer"


class TransferDataLoader:
    def __init__(self, base_path: str | Path | None = None):
        self._base_path = Path(base_path) if base_path else BASE_PATH
        self._cache: dict[str, dict[str, dict]] = {}

    def _load_file(self, login_id: str, transfer_type: str) -> dict:
        """Load and cache a transfer JSON file."""
        cache_key = f"{login_id}:{transfer_type}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        file_path = self._base_path / login_id / f"transfer_{transfer_type}.json"
        if not file_path.exists():
            raise FileNotFoundError(
                f"Transfer data not found: {file_path}. "
                f"User '{login_id}' may not have '{transfer_type}' transfer data."
            )

        data = json.loads(file_path.read_text(encoding="utf-8"))
        self._cache[cache_key] = data
        return data

    def get_step_data(self, login_id: str, transfer_type: str, step_key: str) -> dict | None:
        """Return data for a specific API step.

        Returns None if the step_key exists but its value is null (user ineligible).
        Raises FileNotFoundError if the JSON file doesn't exist.
        Raises KeyError if the step_key doesn't exist in the file.
        """
        data = self._load_file(login_id, transfer_type)
        if step_key not in data:
            raise KeyError(
                f"Step '{step_key}' not found in transfer_{transfer_type}.json for user '{login_id}'. "
                f"Available keys: {list(data.keys())}"
            )
        value = data[step_key]
        if value is None:
            return None
        return value

    def get_available_users(self) -> list[str]:
        """Scan base_path for subdirectories = user IDs."""
        if not self._base_path.exists():
            return []
        return sorted([
            d.name for d in self._base_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

    def get_user_transfer_types(self, login_id: str) -> list[str]:
        """Return which transfer types have JSON files for this user."""
        user_dir = self._base_path / login_id
        if not user_dir.exists():
            return []
        types = []
        for f in sorted(user_dir.glob("transfer_*.json")):
            # Extract type from "transfer_m2m.json" → "m2m"
            t = f.stem.replace("transfer_", "")
            types.append(t)
        return types

    def clear_cache(self):
        """Clear the file cache."""
        self._cache.clear()


# Singleton instance
_loader: TransferDataLoader | None = None


def get_transfer_data_loader() -> TransferDataLoader:
    global _loader
    if _loader is None:
        _loader = TransferDataLoader()
    return _loader

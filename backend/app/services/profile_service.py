import json
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "profile"

# In-memory store: keyed by customerLoginId
_profile_data: dict[str, dict] = {}
# Profile listing cache
_profile_list: list[dict] = []


def _scan_profiles():
    """Scan profile directory and build the profile list."""
    global _profile_list
    _profile_list = []

    for f in sorted(PROFILE_DIR.glob("*_profile.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            profile = data.get("profile", {})
            user_id_info = profile.get("userIdentifier", {})
            login_id = user_id_info.get("customerLoginId", "")
            name_info = profile.get("profileName", {})
            name = name_info.get("userName") or name_info.get("firstName") or login_id

            if not login_id:
                continue

            accounts = data.get("accounts", [])
            rewards = profile.get("rewardsProfile", {})

            _profile_list.append({
                "login_id": login_id,
                "name": name,
                "city": profile.get("mailingAddress", {}).get("city", ""),
                "state": profile.get("mailingAddress", {}).get("state", {}).get("value", ""),
                "segment": profile.get("businessSegment", {}).get("name", ""),
                "tier": rewards.get("tierDisplayName") or "Standard",
                "account_count": len(accounts),
                "profile_file": f.name,
            })
        except (json.JSONDecodeError, KeyError):
            continue


def _get_file_prefix(profile_info: dict) -> str:
    """Get the file prefix from a profile info dict."""
    return profile_info["profile_file"].replace("_bank_profile.json", "").replace("_profile.json", "")


def list_profiles() -> list[dict]:
    """Return the list of available profiles from the profile directory."""
    if not _profile_list:
        _scan_profiles()
    # Deduplicate by login_id
    seen = set()
    unique = []
    for p in _profile_list:
        if p["login_id"] not in seen:
            seen.add(p["login_id"])
            unique.append(p)
    return unique


def load_profile(login_id: str) -> dict | None:
    """Load full profile + accounts into memory at login. Returns profile summary or None."""
    profiles = list_profiles()
    profile_info = next((p for p in profiles if p["login_id"] == login_id), None)
    if not profile_info:
        return None

    profile_file = PROFILE_DIR / profile_info["profile_file"]
    profile_data = json.loads(profile_file.read_text(encoding="utf-8"))

    _profile_data[login_id] = {
        "profile": profile_data.get("profile", {}),
        "accounts": profile_data.get("accounts", []),
        "file_prefix": _get_file_prefix(profile_info),
    }

    return profile_info


def get_profile(login_id: str) -> dict | None:
    """Get the profile section for a user."""
    data = _profile_data.get(login_id)
    if not data:
        return None
    return data["profile"]


def get_accounts(login_id: str) -> list[dict]:
    """Get the accounts for a user."""
    data = _profile_data.get(login_id)
    if not data:
        return []
    return data["accounts"]


def get_file_prefix(login_id: str) -> str | None:
    """Get the file prefix for loading related data (transactions, etc.)."""
    data = _profile_data.get(login_id)
    if not data:
        return None
    return data["file_prefix"]


def is_loaded(login_id: str) -> bool:
    """Check if a profile is loaded in memory."""
    return login_id in _profile_data

import uuid

from fastapi import APIRouter, HTTPException

from app.services.profile_service import list_profiles, load_profile, get_file_prefix
from app.services.transaction_service import load_transactions

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/profiles")
def get_profiles():
    """List all available profiles from the profile directory."""
    return list_profiles()


@router.get("/profiles/{login_id}")
def get_profile(login_id: str):
    """Get profile summary by login ID."""
    profiles = list_profiles()
    profile = next((p for p in profiles if p["login_id"] == login_id), None)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.get("/profiles/{login_id}/full")
def get_profile_full(login_id: str):
    """Read-only inspection endpoint — returns the FULL profile JSON (every
    section under `profile`, every account) plus raw transaction records.
    Does NOT log in or mutate session state. Used by the landing-page
    'View profile JSON / transactions' modal so testers can inspect what
    the system sees for a given user.
    """
    from app.services.profile_service import get_accounts, get_profile as get_profile_section
    from app.services.transaction_service import get_transaction_records

    summary = load_profile(login_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Profile not found")

    prefix = get_file_prefix(login_id)
    if prefix:
        load_transactions(login_id, prefix)

    profile_section = get_profile_section(login_id) or {}
    accounts = get_accounts(login_id) or []
    transactions = get_transaction_records(login_id) or []

    return {
        "summary": summary,
        "profile": profile_section,
        "accounts": accounts,
        "transactions": transactions,
        "transaction_count": len(transactions),
        "account_count": len(accounts),
    }


@router.post("/login/{login_id}")
def do_login(login_id: str):
    """Login as a profile. Loads full profile + transactions into memory."""
    result = load_profile(login_id)
    if not result:
        raise HTTPException(status_code=404, detail="Profile not found")

    # Load transactions into transaction service memory
    prefix = get_file_prefix(login_id)
    if prefix:
        load_transactions(login_id, prefix)

    token = str(uuid.uuid4())
    return {
        "token": token,
        "profile": result,
    }

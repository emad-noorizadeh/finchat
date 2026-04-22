"""TransferOpsTool — unified AgentTool wrapping TransferService.

Replaces the `_make_transfer_tool_caller` closure in transfer_tool.py.
Exposes every TransferService method as a discoverable action so the
Transfer sub-agent's `tool_call_node`s — and the Agent Builder UI — can
see what's available and what each action takes / returns.

All actions run as the user whose id is passed via `context["user_id"]`.
Failures are returned as ERROR dicts (not raised) so the dispatcher's
predicates can match on `variables.X.status == 'ERROR'`.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.transfer_service import ScheduleType, TransferService, TransferType
from app.services.transfer_data_loader import get_transfer_data_loader
from app.tools.agent_tool import AgentTool, action, register_agent_tool

logger = logging.getLogger(__name__)


def _svc() -> TransferService:
    return TransferService(get_transfer_data_loader())


def _err(category: str, internal: str, user_facing_message: str | None = None) -> dict:
    return {
        "status": "ERROR",
        "error_category": category,
        "error": internal,
        "user_facing_message": user_facing_message
        or "Something went wrong. Please try again.",
    }


def _coerce_transfer_type(value: Any, default: str = "m2m") -> TransferType:
    try:
        return TransferType(str(value or default))
    except ValueError:
        return TransferType(default)


def _coerce_schedule(value: Any, default: str = "IMMEDIATE") -> ScheduleType:
    try:
        return ScheduleType(str(value or default))
    except ValueError:
        return ScheduleType(default)


# --- Shared schema fragments ---

_TRANSFER_TYPE_SCHEMA = {
    "type": "string",
    "enum": ["m2m", "cc", "zelle"],
    "description": "m2m: between user's own accounts · cc: credit-card payment · zelle: person-to-person",
    "default": "m2m",
}

_SCHEDULE_SCHEMA = {
    "type": "string",
    "enum": ["IMMEDIATE", "RECURRING", "ONE_TIME_SCHEDULED"],
    "default": "IMMEDIATE",
}

_ACCOUNT_ID_SCHEMA = {
    "type": "string",
    "description": "accountTempId of the account (see sourceAccounts/destinationAccounts on get_details)",
}


class TransferOpsTool(AgentTool):
    """Transfer operations — every TransferService method as an action."""

    name = "transfer"
    agent_name = "transfer_money"
    description = (
        "Domestic transfer operations. Use get_details first to pull "
        "eligible source + destination accounts, then validate → submit."
    )
    scope = "sub_agent"

    @action(
        "get_details",
        description="Load eligible source + destination accounts (or payee list for Zelle) for this user.",
        params_schema={
            "type": "object",
            "properties": {"transfer_type": _TRANSFER_TYPE_SCHEMA},
        },
        output_schema={
            "type": "object",
            "properties": {
                "sourceAccounts": {"type": "array"},
                "destinationAccounts": {"type": "array"},
                "payeeList": {"type": "array"},
            },
        },
    )
    async def get_details(self, params: dict, context: dict) -> dict:
        user_id = context.get("user_id", "")
        tt = _coerce_transfer_type(params.get("transfer_type"))
        try:
            return _svc().get_transfer_details(user_id, tt)
        except (ValueError, FileNotFoundError, KeyError) as e:
            return _err("system", f"get_transfer_details: {e}")

    @action(
        "get_pair",
        description="Return eligible targets given a chosen source account (m2m / cc only — Zelle uses get_details).",
        params_schema={
            "type": "object",
            "properties": {
                "transfer_type": _TRANSFER_TYPE_SCHEMA,
                "source_account_id": _ACCOUNT_ID_SCHEMA,
            },
            "required": ["source_account_id"],
        },
    )
    async def get_pair(self, params: dict, context: dict) -> dict:
        user_id = context.get("user_id", "")
        tt = _coerce_transfer_type(params.get("transfer_type"))
        src = str(params.get("source_account_id", ""))
        if not src:
            return _err("validation", "source_account_id is required")
        try:
            return _svc().get_transfer_pair(user_id, src, tt)
        except (ValueError, FileNotFoundError, KeyError) as e:
            return _err("system", f"get_transfer_pair: {e}")

    @action(
        "get_options",
        description="Return scheduling options, calendar, and payment options for a chosen source/target pair.",
        params_schema={
            "type": "object",
            "properties": {
                "transfer_type": _TRANSFER_TYPE_SCHEMA,
                "source_account_id": _ACCOUNT_ID_SCHEMA,
                "target_account_id": _ACCOUNT_ID_SCHEMA,
                "target_payee_ref": {"type": "string"},
            },
            "required": ["source_account_id"],
        },
    )
    async def get_options(self, params: dict, context: dict) -> dict:
        user_id = context.get("user_id", "")
        tt = _coerce_transfer_type(params.get("transfer_type"))
        src = str(params.get("source_account_id", ""))
        if not src:
            return _err("validation", "source_account_id is required")
        try:
            return _svc().get_transfer_options(
                user_id, src,
                target_account_id=params.get("target_account_id") or None,
                target_payee_ref=params.get("target_payee_ref") or None,
                transfer_type=tt,
            )
        except (ValueError, FileNotFoundError, KeyError) as e:
            return _err("system", f"get_transfer_options: {e}")

    @action(
        "validate",
        description="Pre-submit validation. Returns _validation_id + a review object with warnings/disclaimers. Nothing is committed.",
        params_schema={
            "type": "object",
            "properties": {
                "transfer_type": _TRANSFER_TYPE_SCHEMA,
                "source_account_id": _ACCOUNT_ID_SCHEMA,
                "target_account_id": _ACCOUNT_ID_SCHEMA,
                "target_payee_ref": {"type": "string"},
                "amount": {"type": "number", "minimum": 0.01},
                "schedule_type": _SCHEDULE_SCHEMA,
            },
            "required": ["source_account_id", "amount"],
        },
    )
    async def validate(self, params: dict, context: dict) -> dict:
        user_id = context.get("user_id", "")
        tt = _coerce_transfer_type(params.get("transfer_type"))
        st = _coerce_schedule(params.get("schedule_type"))
        src = str(params.get("source_account_id", ""))
        try:
            amt = float(params.get("amount") or 0)
        except (TypeError, ValueError):
            return _err("validation", "amount must be a number")
        if not (src and amt > 0):
            return _err("validation", "source_account_id and amount > 0 are required")
        try:
            result = _svc().validate_transfer(
                user_id, src,
                target_account_id=params.get("target_account_id") or None,
                target_payee_ref=params.get("target_payee_ref") or None,
                amount=amt, schedule_type=st, transfer_type=tt,
            )
        except (ValueError, FileNotFoundError, KeyError) as e:
            return _err("validation", f"validate_transfer: {e}")
        if not result.get("_validation_id"):
            return _err(
                "policy",
                f"no validation_id: {result}",
                user_facing_message=result.get("message")
                or "The bank didn't approve that transfer.",
            )
        return result

    @action(
        "submit",
        description="Commit the transfer using a validation_id from `validate`. This is the irreversible step.",
        params_schema={
            "type": "object",
            "properties": {
                "transfer_type": _TRANSFER_TYPE_SCHEMA,
                "source_account_id": _ACCOUNT_ID_SCHEMA,
                "target_account_id": _ACCOUNT_ID_SCHEMA,
                "target_payee_ref": {"type": "string"},
                "amount": {"type": "number", "minimum": 0.01},
                "schedule_type": _SCHEDULE_SCHEMA,
                "validation_id": {"type": "string", "description": "From a prior `validate` call."},
            },
            "required": ["source_account_id", "amount", "validation_id"],
        },
    )
    async def submit(self, params: dict, context: dict) -> dict:
        user_id = context.get("user_id", "")
        tt = _coerce_transfer_type(params.get("transfer_type"))
        st = _coerce_schedule(params.get("schedule_type"))
        src = str(params.get("source_account_id", ""))
        val_id = str(params.get("validation_id", ""))
        try:
            amt = float(params.get("amount") or 0)
        except (TypeError, ValueError):
            return _err("validation", "amount must be a number")
        if not (src and amt > 0 and val_id):
            return _err("validation", "source_account_id, amount, validation_id are required")
        try:
            result = _svc().submit_transfer(
                user_id, src,
                target_account_id=params.get("target_account_id") or None,
                target_payee_ref=params.get("target_payee_ref") or None,
                amount=amt, schedule_type=st, validation_id=val_id,
                transfer_type=tt,
            )
        except (ValueError, FileNotFoundError, KeyError) as e:
            return _err("transient", f"submit_transfer: {e}")
        status = result.get("status", "COMPLETED")
        if status != "COMPLETED":
            return _err(
                "policy",
                f"submit status={status!r}",
                user_facing_message=result.get("message")
                or "The transfer couldn't be completed.",
            )
        return result

    @action(
        "resolve_account",
        description="Fuzzy-match a user hint (e.g. 'checking', 'savings', last-4 digits) against a list of candidate accounts. Returns the matched account dict, or an ERROR if no match.",
        params_schema={
            "type": "object",
            "properties": {
                "hint": {"type": "string", "description": "Free-text hint from the user — 'checking', 'credit card', last-4 digits, etc."},
                "candidates": {"type": "array", "description": "List of account dicts (sourceAccounts or destinationAccounts from get_details)."},
            },
            "required": ["hint", "candidates"],
        },
    )
    async def resolve_account(self, params: dict, context: dict) -> dict:
        hint = (params.get("hint") or "").strip()
        candidates = params.get("candidates") or []
        if not hint:
            return _err("validation", "hint is empty")
        if not isinstance(candidates, list) or not candidates:
            return _err("validation", "candidates must be a non-empty list")
        h = hint.lower()
        last4 = "".join(c for c in hint if c.isdigit())
        for c in candidates:
            label = (c.get("accountLabel") or "").lower()
            variant = (c.get("offeringVariant") or "").upper()
            if last4 and last4 in label:
                return c
            if h in label:
                return c
            if h in {"checking", "check"} and variant == "CK":
                return c
            if h in {"savings", "saving", "save"} and variant == "SV":
                return c
            if "money market" in h and variant == "MA":
                return c
            if "credit" in h and variant == "CC":
                return c
        return _err(
            "validation",
            f"no account matched hint {hint!r}",
            user_facing_message=f"I couldn't match '{hint}' to one of your accounts.",
        )


# Registration happens when app.tools.init_tools() runs.
register_agent_tool(TransferOpsTool())

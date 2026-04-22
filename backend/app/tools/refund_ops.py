"""RefundOpsTool — unified AgentTool wrapping RefundService.

Mirrors TransferOpsTool: one namespace (`refund`), multiple declared
actions (`list_fees`, `submit_refund`). The `refund_fee` sub-agent's
tool_call_nodes address it as `{tool: "refund", action: "<name>"}`.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.refund_data_loader import get_refund_data_loader
from app.services.refund_service import RefundService
from app.tools.agent_tool import AgentTool, action, register_agent_tool

logger = logging.getLogger(__name__)


def _svc() -> RefundService:
    return RefundService(get_refund_data_loader())


def _err(category: str, internal: str, user_facing_message: str | None = None) -> dict:
    return {
        "status": "ERROR",
        "error_category": category,
        "error": internal,
        "user_facing_message": user_facing_message or "Something went wrong. Please try again.",
    }


class RefundOpsTool(AgentTool):
    """Fee-refund operations: list eligible fees and submit a refund."""

    name = "refund"
    agent_name = "refund_fee"
    description = (
        "Fee-refund operations. Call list_fees first to see eligible refundable "
        "fees for the logged-in user, then submit_refund with one of their "
        "activityReferences. The decision (APPROVED or DENIED) is determined "
        "by fee type."
    )
    scope = "sub_agent"

    @action(
        "list_fees",
        description=(
            "Return the refundable-fees payload for the user: account details + "
            "a list of fees with activityReference, feeType, amount, originDate."
        ),
        params_schema={"type": "object", "properties": {}},
        output_schema={
            "type": "object",
            "properties": {
                "eligible": {"type": "boolean"},
                "accountDetails": {"type": "object"},
                "refundableTransactions": {"type": "array"},
                "totalRefundableAmount": {"type": "number"},
                "refundableTransactionCount": {"type": "number"},
            },
        },
    )
    async def list_fees(self, params: dict, context: dict) -> dict:
        user_id = context.get("user_id", "")
        if not user_id:
            return _err("auth", "missing user_id in context")
        try:
            return _svc().get_fee_transactions(user_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[refund_list_fees_failed] user=%s err=%s", user_id, e)
            return _err("system", f"list_fees: {e}")

    @action(
        "resolve_fee",
        description=(
            "Pick one fee from a candidate list using a free-text hint (fee "
            "type name) OR a 1-based index. Returns the selected fee dict or "
            "ERROR if nothing matched."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "hint":       {"type": "string",  "description": "Free-text fee-type hint — 'late fee', 'cash advance', etc."},
                "index":      {"type": "number",  "description": "1-based index into the candidates list."},
                "candidates": {"type": "array",   "description": "refundableTransactions from list_fees."},
            },
            "required": ["candidates"],
        },
    )
    async def resolve_fee(self, params: dict, context: dict) -> dict:
        candidates = (params or {}).get("candidates") or []
        if not isinstance(candidates, list) or not candidates:
            return _err("validation", "candidates must be a non-empty list")

        # Index path wins if provided and in-range.
        idx = (params or {}).get("index")
        if isinstance(idx, (int, float)) and idx:
            pos = int(idx) - 1
            if 0 <= pos < len(candidates):
                return candidates[pos]

        hint = str((params or {}).get("hint") or "").strip().lower()
        if not hint:
            return _err(
                "validation",
                "hint is empty and index missing",
                user_facing_message="I need to know which fee — late fee or cash advance?",
            )

        # Match on feeType first (strict), then fall back to phrase match on
        # primaryDescription / statusDescription.
        def _matches(t: dict) -> bool:
            ft = (t.get("feeType") or "").lower()
            pd = (t.get("primaryDescription") or "").lower()
            sd = (t.get("statusDescription") or "").lower()
            if "late" in hint     and ("late" in ft or "late" in pd):       return True
            if "cash advance" in hint and ("cash_advance" in ft or "cash advance" in pd): return True
            if "interest" in hint and "interest" in pd:                     return True
            if "annual" in hint   and "annual" in ft:                       return True
            if hint in ft or hint in pd or hint in sd:                      return True
            return False

        for t in candidates:
            if _matches(t):
                return t

        return _err(
            "validation",
            f"no fee matched hint={hint!r}",
            user_facing_message=f"I couldn't find a fee matching '{hint}'.",
        )

    @action(
        "submit_refund",
        description=(
            "Submit a refund request for a specific fee. Decision is deterministic: "
            "LATE_FEE → APPROVED; CASH_ADVANCE_INTEREST → DENIED. Returns the "
            "full evaluation response with conditionsEvaluated, refundTrackingId "
            "(if approved), and postRefundBalance (if approved)."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "activity_reference": {
                    "type": "string",
                    "description": "activityReference of the fee to refund (from list_fees.refundableTransactions[].activityReference).",
                },
            },
            "required": ["activity_reference"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "refundDecision": {"type": "string", "enum": ["APPROVED", "DENIED"]},
                "refundAmount": {"type": "number"},
                "decisionReason": {"type": "string"},
                "evaluationCode": {"type": "string"},
                "conditionsEvaluated": {"type": "array"},
                "refundTrackingId": {"type": ["string", "null"]},
                "effectiveDate": {"type": ["string", "null"]},
                "postRefundBalance": {"type": ["number", "null"]},
                "activityReference": {"type": "string"},
            },
        },
    )
    async def submit_refund(self, params: dict, context: dict) -> dict:
        user_id = context.get("user_id", "")
        activity_ref = (params or {}).get("activity_reference") or ""
        if not user_id:
            return _err("auth", "missing user_id in context")
        if not activity_ref:
            return _err("validation", "activity_reference is required")
        try:
            return _svc().submit_refund(user_id, activity_ref)
        except Exception as e:  # noqa: BLE001
            logger.warning("[refund_submit_failed] user=%s ref=%s err=%s", user_id, activity_ref, e)
            return _err("system", f"submit_refund: {e}")


register_agent_tool(RefundOpsTool())

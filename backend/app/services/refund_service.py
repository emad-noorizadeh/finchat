"""Fee-refund service — mocked API layer.

Mirrors TransferService: multi-step flow, deterministic decisions driven
by the user's mock JSON + the fee type of the transaction being refunded.

Flow:
  1. get_fee_transactions(user_id) — lists refundable fees + account context.
     Users with no data on disk (e.g. Chris) get an empty eligible list.
  2. submit_refund(user_id, activity_reference) — decides APPROVED vs DENIED
     based on the transaction's fee_type (late fee → approved, cash advance
     interest → denied). Returns the corresponding mock response template
     with the transaction's amount populated.
"""

import copy
import uuid
from datetime import date

from app.services.refund_data_loader import RefundDataLoader


class RefundDecisionCode(str):
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class RefundService:
    """Thin, deterministic mock — no network."""

    def __init__(self, data_loader: RefundDataLoader):
        self.loader = data_loader

    # --- Step 1: list fees ---

    def get_fee_transactions(self, login_id: str) -> dict:
        """Return the refundable-fees payload for the user.

        Empty / missing-user case: returns an eligible=false envelope so
        callers can branch on `eligible` without catching exceptions.
        """
        data = self.loader.get_step_data(login_id, "fee_transactions")
        if data is None:
            return {
                "eligible": False,
                "message": (
                    "No refundable fees on file for this customer."
                ),
                "accountDetails": None,
                "refundableTransactions": [],
                "totalRefundableAmount": 0.0,
                "refundableTransactionCount": 0,
            }
        return {"eligible": True, **data}

    # --- Step 2: submit refund ---

    def submit_refund(
        self,
        login_id: str,
        activity_reference: str,
    ) -> dict:
        """Decide + return the refund response for a specific fee.

        Decision rules (mocked):
          - fee_type LATE_FEE                → APPROVED
          - fee_type CASH_ADVANCE_INTEREST   → DENIED
          - everything else                   → DENIED (no mock)

        The corresponding template (refund_approved / refund_denied) is
        deep-copied so we can safely inject a fresh refundTrackingId +
        effectiveDate and the real amount from the selected transaction.
        """
        fees = self.loader.get_step_data(login_id, "fee_transactions")
        if fees is None:
            return self._decision_envelope(
                RefundDecisionCode.DENIED,
                message="No refundable fees on file for this customer.",
            )

        transactions = fees.get("refundableTransactions") or []
        selected = next(
            (t for t in transactions if t.get("activityReference") == activity_reference),
            None,
        )
        if selected is None:
            return self._decision_envelope(
                RefundDecisionCode.DENIED,
                message=f"Activity {activity_reference!r} not found among refundable transactions.",
            )

        fee_type = (selected.get("feeType") or "").upper()
        try:
            amount = float(str(selected.get("authorizedAmount") or 0).replace("$", "").replace(",", ""))
        except (TypeError, ValueError):
            amount = 0.0

        if fee_type == "LATE_FEE":
            template = self.loader.get_step_data(login_id, "refund_approved") or {}
            return self._build_response(
                template,
                decision=RefundDecisionCode.APPROVED,
                amount=amount,
                selected=selected,
                account=fees.get("accountDetails") or {},
            )

        # All other fee types (including CASH_ADVANCE_INTEREST) → denied.
        template = self.loader.get_step_data(login_id, "refund_denied") or {}
        return self._build_response(
            template,
            decision=RefundDecisionCode.DENIED,
            amount=amount,
            selected=selected,
            account=fees.get("accountDetails") or {},
        )

    # --- helpers ---

    @staticmethod
    def _decision_envelope(decision: str, message: str) -> dict:
        return {
            "refundDecision": decision,
            "refundAmount": 0.0,
            "decisionReason": message,
            "evaluationCode": "EVAL-NO-DATA",
            "conditionsEvaluated": [],
            "refundTrackingId": None,
            "effectiveDate": None,
            "postRefundBalance": None,
        }

    @staticmethod
    def _build_response(template: dict, *, decision: str, amount: float, selected: dict, account: dict) -> dict:
        result = copy.deepcopy(template) if template else {}
        result["refundDecision"] = decision
        if decision == RefundDecisionCode.APPROVED:
            # Inject fresh tracking + computed post-balance from this transaction.
            result["refundAmount"] = amount
            result["refundTrackingId"] = _make_tracking_id(account.get("displayAccountReference", ""))
            result["effectiveDate"] = date.today().isoformat()
            credit = account.get("creditLineInfo") or {}
            current = credit.get("currentBalance")
            if isinstance(current, (int, float)):
                result["postRefundBalance"] = round(current - amount, 2)
        else:
            result["refundAmount"] = 0.0
            result["refundTrackingId"] = None
            result["effectiveDate"] = None
            result["postRefundBalance"] = None

        # Carry the selected activity reference back so callers can correlate.
        result["activityReference"] = selected.get("activityReference")
        return result


def _make_tracking_id(account_ref: str) -> str:
    suffix = (account_ref or "")[-4:] or "0000"
    today = date.today().strftime("%Y%m%d")
    short = uuid.uuid4().hex[:6].upper()
    return f"RFD-{today}-CC{suffix}-{short}"

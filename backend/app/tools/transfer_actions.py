"""Agent-scoped unified transfer tool — used only by the TransferSubAgent.

NOT registered in the main tool registry. This is a LangChain @tool function
bound directly to the sub-agent's LLM. Same unified design as the orchestrator-level
transfer tool — one tool, action parameter.
"""

import json

from langchain_core.tools import tool

from app.tools.base import tool_meta
from app.services.transfer_data_loader import get_transfer_data_loader
from app.services.transfer_service import TransferService, TransferType, ScheduleType

_service = None
_current_user_id: str | None = None


def set_current_user(user_id: str):
    global _current_user_id
    _current_user_id = user_id


def _get_service() -> TransferService:
    global _service
    if _service is None:
        _service = TransferService(get_transfer_data_loader())
    return _service


@tool
@tool_meta(
    widget="transfer_confirmation",
    flow=[
        "get_details → list available accounts and payees",
        "get_pair → find eligible targets for a source account",
        "get_options → get scheduling and payment options",
        "validate → validate transfer params, returns validation_id",
        "submit → execute transfer using validation_id",
    ],
    validations=[
        "transfer_type must be: m2m, cc, or zelle",
        "schedule_type must be: IMMEDIATE, RECURRING, or ONE_TIME_SCHEDULED",
        "validation_id required for submit action",
        "amount must be positive",
    ],
    errors=[
        "Returns error for invalid transfer_type or action",
        "Catches ValueError, FileNotFoundError, KeyError from service",
        "Returns error if user not logged in",
    ],
    is_read_only=False,
    is_concurrency_safe=False,
    agent="transfer",
)
def transfer_money(
    action: str,
    transfer_type: str,
    source_account_id: str = "",
    target_account_id: str = "",
    target_payee_ref: str = "",
    amount: float = 0.0,
    schedule_type: str = "IMMEDIATE",
    validation_id: str = "",
) -> str:
    """Transfer money between accounts, pay a credit card, or send via Zelle.

    This is a multi-step tool. Call with different actions to progress:
    - action="get_details": See available accounts/payees
    - action="get_pair": Get eligible targets for a source (m2m/cc only)
    - action="get_options": Get scheduling and payment options
    - action="validate": Validate before submitting (returns validation_id)
    - action="submit": Execute the transfer (requires validation_id)

    Args:
        action: Which step to execute (get_details, get_pair, get_options, validate, submit)
        transfer_type: "m2m" (between own accounts), "cc" (credit card), "zelle" (send to person)
        source_account_id: accountTempId of the source/funding account
        target_account_id: accountTempId of the destination (m2m/cc)
        target_payee_ref: payeeReferenceId of the Zelle recipient
        amount: Transfer amount in dollars
        schedule_type: IMMEDIATE, RECURRING, or ONE_TIME_SCHEDULED
        validation_id: From the validate step (required for submit)
    """
    service = _get_service()
    user_id = _current_user_id or ""

    try:
        tt = TransferType(transfer_type)
    except ValueError:
        return json.dumps({"error": f"Invalid transfer type: '{transfer_type}'. Use 'm2m', 'cc', or 'zelle'."})

    try:
        st = ScheduleType(schedule_type)
    except ValueError:
        st = ScheduleType.IMMEDIATE

    try:
        if action == "get_details":
            result = service.get_transfer_details(user_id, tt)
        elif action == "get_pair":
            result = service.get_transfer_pair(user_id, source_account_id, tt)
        elif action == "get_options":
            result = service.get_transfer_options(
                user_id, source_account_id, transfer_type=tt,
            )
        elif action == "validate":
            result = service.validate_transfer(
                user_id, source_account_id,
                target_account_id=target_account_id or None,
                target_payee_ref=target_payee_ref or None,
                amount=amount, schedule_type=st, transfer_type=tt,
            )
        elif action == "submit":
            result = service.submit_transfer(
                user_id, source_account_id,
                target_account_id=target_account_id or None,
                target_payee_ref=target_payee_ref or None,
                amount=amount, schedule_type=st,
                validation_id=validation_id, transfer_type=tt,
            )
        else:
            return json.dumps({"error": f"Unknown action: '{action}'"})

        # For submit action — return dual output with widget
        if action == "submit" and result.get("status") == "COMPLETED":
            widget = {
                "widget": "transfer_confirmation",
                "title": "Transfer Complete",
                "icon": "check-circle",
                "data": {
                    "from": source_account_id[:20],
                    "to": (target_account_id or target_payee_ref or "")[:20],
                    "amount": amount,
                    "date": result.get("effective_date", ""),
                    "confirmation_id": result.get("confirmation_id", ""),
                    "status": "COMPLETED",
                },
                "actions": [{"id": "dismiss", "label": "Done", "style": "primary", "type": "dismiss"}],
                "metadata": {"status": "COMPLETED"},
            }
            return json.dumps({
                "_tool_result": True,
                "to_llm": f"Transfer confirmed: {result.get('confirmation_id','')}, ${amount:,.2f}, effective {result.get('effective_date','')}",
                "widget": widget,
            })

        return json.dumps(result)

    except (ValueError, FileNotFoundError, KeyError) as e:
        return json.dumps({"error": str(e)})

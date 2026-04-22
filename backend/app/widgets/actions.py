"""Simple widget action handlers.

For graph-resuming actions (confirm/cancel on confirmation_request),
the chat router handles it via the resume flow — not here.
"""

from app.models.widget_instance import WidgetInstance
from app.services.widget_service import WidgetService


def _resolve_user_id(instance: WidgetInstance) -> str:
    """Prefer extra_data.user_id (stamped by tool_execute at widget creation).
    Older widgets from before that fix may have empty extra_data; fall back to
    looking up the chat session's user_id."""
    extra = instance.extra_data or {}
    uid = extra.get("user_id") or ""
    if uid:
        return uid
    # Fallback: session lookup.
    try:
        from app.database import get_session_context
        from app.models.chat import ChatSession
        with get_session_context() as db:
            session = db.get(ChatSession, instance.session_id)
            if session and getattr(session, "user_id", None):
                return session.user_id
    except Exception:  # noqa: BLE001
        pass
    return ""


def handle_action(ws: WidgetService, instance: WidgetInstance, action_id: str, payload: dict) -> WidgetInstance | None:
    """Route to the correct handler based on action_id + widget_type."""
    # transfer_form widget drives a three-stage flow:
    #   form    →  validate  →  review  →  submit  →  completed
    # Supports a "back" step from review to form for edits, and cancel.
    if instance.widget_type == "transfer_form":
        if action_id == "validate":
            return _handle_transfer_validate(ws, instance, payload)
        if action_id == "submit":
            return _handle_transfer_submit(ws, instance, payload)
        if action_id == "back":
            return _handle_transfer_back(ws, instance, payload)
        if action_id == "cancel":
            return ws.update_status(instance.id, "dismissed")

    # refund_form widget: three-stage flow driven entirely by action handlers.
    #   select_fee → select → review → submit → completed
    if instance.widget_type == "refund_form":
        if action_id == "select":
            return _handle_refund_select(ws, instance, payload)
        if action_id == "submit":
            return _handle_refund_submit(ws, instance, payload)
        if action_id == "back":
            return _handle_refund_back(ws, instance, payload)
        if action_id == "cancel":
            return ws.update_status(instance.id, "dismissed")

    handlers = {
        "dismiss": _handle_dismiss,
        "load_more": _handle_load_more,
        "retry": _handle_retry,
    }

    handler = handlers.get(action_id)
    if not handler:
        return None

    return handler(ws, instance, payload)


def _handle_transfer_validate(ws: WidgetService, instance: WidgetInstance, payload: dict) -> WidgetInstance:
    """Stage 1 → 2: validate the form inputs. Stores validation_result +
    flips _stage to 'review'. No money moves here."""
    from app.services.transfer_service import ScheduleType, TransferType
    from app.tools.transfer_actions import _get_service, set_current_user

    data = dict(instance.data)
    user_id = _resolve_user_id(instance)
    amount = float(payload.get("amount") or data.get("amount") or 0)
    from_account = payload.get("from_account") or data.get("from_account") or {}
    to_account = payload.get("to_account") or data.get("to_account") or {}

    source_id = from_account.get("accountTempId") or from_account.get("accountReferenceId") or ""
    target_id = to_account.get("accountTempId") or to_account.get("accountReferenceId") or ""
    if not (source_id and target_id and amount > 0):
        data["submit_error"] = "Fill in amount and both accounts."
        return ws.update_data(instance.id, data) or instance

    data["amount"] = amount
    data["from_account"] = from_account
    data["to_account"] = to_account
    data["submit_error"] = ""

    set_current_user(user_id)
    svc = _get_service()
    try:
        val = svc.validate_transfer(
            user_id, source_id, target_account_id=target_id,
            amount=amount, schedule_type=ScheduleType.IMMEDIATE,
            transfer_type=TransferType.M2M,
        )
    except Exception as e:  # noqa: BLE001
        data["submit_error"] = f"Validation failed: {e}"
        return ws.update_data(instance.id, data) or instance

    if not val.get("_validation_id"):
        reason = val.get("message") or val.get("error") or "No validation id returned."
        data["submit_error"] = f"The bank didn't approve that transfer — {reason}"
        return ws.update_data(instance.id, data) or instance

    data["validation_result"] = val
    data["_stage"] = "review"
    return ws.update_data(instance.id, data) or instance


def _handle_transfer_submit(ws: WidgetService, instance: WidgetInstance, payload: dict) -> WidgetInstance:
    """Stage 2 → completed: commit the transfer using the stored validation
    id. Rejects if the user skipped the validate step (no validation_id).
    """
    from app.services.transfer_service import ScheduleType, TransferType
    from app.tools.transfer_actions import _get_service, set_current_user

    data = dict(instance.data)
    user_id = _resolve_user_id(instance)
    val = data.get("validation_result") or {}
    validation_id = val.get("_validation_id") or payload.get("validation_id") or ""
    amount = float(data.get("amount") or 0)
    from_account = data.get("from_account") or {}
    to_account = data.get("to_account") or {}
    source_id = from_account.get("accountTempId") or from_account.get("accountReferenceId") or ""
    target_id = to_account.get("accountTempId") or to_account.get("accountReferenceId") or ""

    if not validation_id:
        data["submit_error"] = "Missing validation — please review the transfer again."
        data["_stage"] = "form"
        return ws.update_data(instance.id, data) or instance

    set_current_user(user_id)
    svc = _get_service()
    try:
        submit = svc.submit_transfer(
            user_id, source_id, target_account_id=target_id,
            amount=amount, schedule_type=ScheduleType.IMMEDIATE,
            validation_id=validation_id, transfer_type=TransferType.M2M,
        )
    except Exception as e:  # noqa: BLE001
        data["submit_error"] = f"Submit failed: {e}"
        ws.update_data(instance.id, data)
        return ws.update_status(instance.id, "failed") or instance

    if submit.get("status") and submit["status"] != "COMPLETED":
        data["submit_error"] = submit.get("message", "The transfer couldn't be completed.")
        ws.update_data(instance.id, data)
        return ws.update_status(instance.id, "failed")

    data["confirmation_id"] = submit.get("confirmation_id", "")
    data["effective_date"] = submit.get("effective_date", "")
    data["_stage"] = "completed"
    ws.update_data(instance.id, data)
    return ws.update_status(instance.id, "completed")


def _handle_transfer_back(ws: WidgetService, instance: WidgetInstance, payload: dict) -> WidgetInstance:
    """Stage 2 → 1: user wants to edit before confirming. Clears validation
    so the next Continue click re-validates against the updated inputs."""
    data = dict(instance.data)
    data.pop("validation_result", None)
    data["_stage"] = "form"
    data["submit_error"] = ""
    return ws.update_data(instance.id, data)


def _handle_dismiss(ws: WidgetService, instance: WidgetInstance, payload: dict) -> WidgetInstance:
    return ws.update_status(instance.id, "dismissed")


def _handle_retry(ws: WidgetService, instance: WidgetInstance, payload: dict) -> WidgetInstance:
    return ws.update_status(instance.id, "pending")


def _handle_load_more(ws: WidgetService, instance: WidgetInstance, payload: dict) -> WidgetInstance:
    """Append next page of data to transaction_list widgets."""
    if instance.widget_type != "transaction_list":
        return instance

    # Get current data
    data = dict(instance.data)
    current_page = data.get("page", 1)
    page_size = data.get("page_size", 10)

    # Fetch next page from transaction service
    try:
        from app.services import transaction_service as ts
        # Need user context — stored in metadata
        user_id = instance.metadata.get("user_id", "")
        all_records = ts.get_transaction_records(user_id)

        next_start = current_page * page_size
        next_records = ts.get_recent_transactions(user_id, page_size, "")

        if next_start < len(all_records):
            # Slice the next page
            next_page = [ts._format_transaction(t) for t in all_records[next_start:next_start + page_size]]
            data["transactions"] = data.get("transactions", []) + next_page
            data["page"] = current_page + 1
    except Exception:
        pass

    return ws.update_data(instance.id, data)


# --- refund_form handlers ---


def _handle_refund_select(ws: WidgetService, instance: WidgetInstance, payload: dict) -> WidgetInstance:
    """Stage 1 → 2: user picked a fee, flip to review. Takes
    `activity_reference` from the widget payload.
    """
    activity_ref = (payload or {}).get("activity_reference") or ""
    data = dict(instance.data)
    fees = data.get("refundable_transactions") or []
    selected = next((t for t in fees if t.get("activityReference") == activity_ref), None)
    if selected is None:
        data["submit_error"] = "Please select a fee before continuing."
        return ws.update_data(instance.id, data) or instance
    data["selected_activity_reference"] = activity_ref
    data["_stage"] = "review"
    data["submit_error"] = ""
    return ws.update_data(instance.id, data) or instance


def _handle_refund_submit(ws: WidgetService, instance: WidgetInstance, payload: dict) -> WidgetInstance:
    """Stage 2 → completed: call RefundService.submit_refund on the
    selected activityReference. Stores the full decision dict in
    data.decision, flips _stage=completed, and updates widget status per
    decision (completed for APPROVED, failed for DENIED)."""
    from app.services.refund_data_loader import get_refund_data_loader
    from app.services.refund_service import RefundService

    user_id = _resolve_user_id(instance)
    data = dict(instance.data)
    activity_ref = data.get("selected_activity_reference") or (payload or {}).get("activity_reference") or ""
    if not (user_id and activity_ref):
        data["submit_error"] = "Missing user or fee selection."
        return ws.update_data(instance.id, data) or instance

    svc = RefundService(get_refund_data_loader())
    try:
        decision = svc.submit_refund(user_id, activity_ref)
    except Exception as e:  # noqa: BLE001
        data["submit_error"] = f"Submit failed: {e}"
        return ws.update_data(instance.id, data) or instance

    data["decision"] = decision
    data["_stage"] = "completed"
    ws.update_data(instance.id, data)
    # The refund REQUEST flow completed regardless of decision — DENIED is a
    # valid terminal outcome, not an error. Keep status="completed" for both
    # APPROVED and DENIED so the UI doesn't show the retryable "failed" banner.
    # The widget's internal card surfaces the decision.
    return ws.update_status(instance.id, "completed") or instance


def _handle_refund_back(ws: WidgetService, instance: WidgetInstance, payload: dict) -> WidgetInstance:
    """Stage 2 → 1: user wants to reselect. Clears the selection."""
    data = dict(instance.data)
    data["selected_activity_reference"] = None
    data["_stage"] = "select_fee"
    data["submit_error"] = ""
    return ws.update_data(instance.id, data) or instance

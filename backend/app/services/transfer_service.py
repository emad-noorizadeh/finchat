import copy
import uuid
from datetime import date, datetime
from enum import Enum

from app.services.transfer_data_loader import TransferDataLoader


class TransferType(str, Enum):
    M2M = "m2m"
    CC = "cc"
    ZELLE = "zelle"


class ScheduleType(str, Enum):
    IMMEDIATE = "IMMEDIATE"
    RECURRING = "RECURRING"
    ONE_TIME_SCHEDULED = "ONE_TIME_SCHEDULED"


class TransferService:
    def __init__(self, data_loader: TransferDataLoader):
        self.loader = data_loader

    def get_transfer_details(self, login_id: str, transfer_type: TransferType) -> dict:
        """Step 1: Return source/destination accounts (m2m/cc) or payee list (zelle)."""
        key = f"transfer_{transfer_type.value}_details"
        try:
            data = self.loader.get_step_data(login_id, transfer_type.value, key)
        except FileNotFoundError as e:
            return {"error": str(e), "eligible": False}

        if data is None:
            return {
                "error": f"User '{login_id}' is not eligible for {transfer_type.value} transfers.",
                "eligible": False,
            }
        return data

    def get_transfer_pair(self, login_id: str, source_account_id: str,
                          transfer_type: TransferType) -> dict:
        """Step 2: Return eligible targets for chosen source (m2m & cc only)."""
        if transfer_type == TransferType.ZELLE:
            return {"error": "Zelle uses payeeList from get_details. Do not call get_pair for Zelle."}

        key = f"transfer_{transfer_type.value}_transfer_pair"
        try:
            data = self.loader.get_step_data(login_id, transfer_type.value, key)
        except (FileNotFoundError, KeyError) as e:
            return {"error": str(e)}

        if data is None:
            return {"targetParticipants": [], "message": "No eligible target accounts found."}
        return data

    def get_transfer_options(self, login_id: str, source_account_id: str,
                             target_account_id: str | None = None,
                             target_payee_ref: str | None = None,
                             transfer_type: TransferType = TransferType.M2M) -> dict:
        """Step 3: Return scheduling options, calendar, card payment options."""
        if transfer_type == TransferType.ZELLE:
            key = f"transfer_zelle_transfer_pair"
        else:
            key = f"transfer_{transfer_type.value}_transferOptions"

        try:
            data = self.loader.get_step_data(login_id, transfer_type.value, key)
        except (FileNotFoundError, KeyError) as e:
            return {"error": str(e)}

        if data is None:
            return {"error": f"Transfer options not available for user '{login_id}'."}
        return data

    def validate_transfer(self, login_id: str, source_account_id: str,
                          target_account_id: str | None = None,
                          target_payee_ref: str | None = None,
                          amount: float = 0.0,
                          schedule_type: ScheduleType = ScheduleType.IMMEDIATE,
                          transfer_type: TransferType = TransferType.M2M) -> dict:
        """Step 4: Pre-submit validation. Returns template with fresh IDs."""
        key = f"transfer_{transfer_type.value}_validate"
        try:
            data = self.loader.get_step_data(login_id, transfer_type.value, key)
        except (FileNotFoundError, KeyError) as e:
            return {"error": str(e)}

        if data is None:
            return {"error": f"User '{login_id}' is not eligible for {transfer_type.value} transfers."}

        # Deep copy to avoid mutating cached data
        result = copy.deepcopy(data)

        # Inject dynamic values
        validation_id = str(uuid.uuid4())
        today = date.today().isoformat()

        if "id" in result:
            result["id"] = validation_id
        if "review" in result and isinstance(result["review"], dict):
            result["review"]["transactionDate"] = today
            result["review"]["transactionType"] = schedule_type.value
        # Store the validation ID at top level for easy access
        result["_validation_id"] = validation_id
        result["_status"] = "READY_TO_SUBMIT"

        # Add warnings/disclaimers based on transfer details
        warnings = []
        disclaimers = []

        if amount > 0:
            # Check insufficient funds (mock: flag if amount > 50000)
            if amount > 50000:
                warnings.append("INSUFFICIENT_FUNDS")
                disclaimers.append("The transfer amount may exceed your available balance. You may be charged an overdraft fee.")

            # Check amount limits (mock: flag if amount > 100000)
            if amount > 100000:
                warnings.append("AMOUNT_EXCEEDS_LIMIT")
                disclaimers.append(f"The maximum transfer amount is $100,000.00. Please enter a lower amount.")

        if transfer_type == TransferType.ZELLE:
            disclaimers.append("Make sure you're sending to someone you trust, and their information is correct. Once you've sent money, you can't cancel it.")

        if transfer_type == TransferType.CC:
            disclaimers.append("Please ensure your funding account has sufficient balance to avoid overdraft fees.")

        result["_warnings"] = warnings
        result["_disclaimers"] = disclaimers

        return result

    def submit_transfer(self, login_id: str, source_account_id: str,
                        target_account_id: str | None = None,
                        target_payee_ref: str | None = None,
                        amount: float = 0.0,
                        schedule_type: ScheduleType = ScheduleType.IMMEDIATE,
                        validation_id: str = "",
                        transfer_type: TransferType = TransferType.M2M) -> dict:
        """Step 5: Execute transfer. Returns request + response with dynamic values."""
        tt = transfer_type.value

        try:
            request_data = self.loader.get_step_data(login_id, tt, f"transfer_{tt}_submit_request")
            response_data = self.loader.get_step_data(login_id, tt, f"transfer_{tt}_submit_response")
        except (FileNotFoundError, KeyError) as e:
            return {"error": str(e)}

        if request_data is None or response_data is None:
            return {"error": f"User '{login_id}' is not eligible for {tt} transfers."}

        request = copy.deepcopy(request_data)
        response = copy.deepcopy(response_data)

        # Inject dynamic values into request
        today = date.today().isoformat()
        confirmation_id = f"CNF-{uuid.uuid4().hex[:12].upper()}"

        _deep_set(request, "id", validation_id or str(uuid.uuid4()))
        _deep_set(request, "amount", amount)
        _deep_set(request, "transactionType", schedule_type.value)

        # Inject dynamic values into response
        _deep_set(response, "confirmationId", confirmation_id)
        _deep_set(response, "confirmationNumber", confirmation_id)
        _deep_set(response, "effectiveDate", today)
        _deep_set(response, "amount", amount)

        return {
            "status": "COMPLETED",
            "request": request,
            "response": response,
            "confirmation_id": confirmation_id,
            "effective_date": today,
        }


def _deep_set(d: dict, key: str, value) -> bool:
    """Set a key in a dict, searching nested dicts. Returns True if found and set."""
    if not isinstance(d, dict):
        return False
    if key in d:
        d[key] = value
        return True
    for v in d.values():
        if isinstance(v, dict) and _deep_set(v, key, value):
            return True
    return False

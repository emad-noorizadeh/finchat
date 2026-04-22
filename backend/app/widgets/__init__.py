from typing import Any
from pydantic import BaseModel


class WidgetAction(BaseModel):
    id: str
    label: str
    style: str = "secondary"  # primary | secondary | danger | success
    endpoint: str | None = None
    method: str = "POST"
    confirm_message: str | None = None
    type: str | None = None  # resume | paginate | dismiss | endpoint


class WidgetResponse(BaseModel):
    widget: str  # Widget type identifier
    title: str = ""
    icon: str | None = None
    data: dict = {}
    actions: list[WidgetAction] = []
    metadata: dict = {}


# Per-widget-type data schemas for validation
WIDGET_SCHEMAS: dict[str, dict] = {
    "transaction_list": {"required": ["transactions"]},
    "account_summary": {"required": ["accounts"]},
    "transfer_confirmation": {"required": ["from", "to", "amount", "confirmation_id", "status"]},
    "profile_card": {"required": ["name"]},
    "confirmation_request": {"required": ["details"]},
    "text_card": {"required": ["content"]},
}


def validate_widget_data(widget_type: str, data: dict) -> list[str]:
    """Validate widget data against its schema. Returns list of missing fields."""
    schema = WIDGET_SCHEMAS.get(widget_type)
    if not schema:
        return [f"Unknown widget type: {widget_type}"]
    return [f for f in schema.get("required", []) if f not in data]


def is_widget_response(data: dict) -> bool:
    """Check if a dict is a widget response."""
    return isinstance(data, dict) and "widget" in data and data["widget"] in WIDGET_SCHEMAS

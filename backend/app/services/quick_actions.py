"""Quick-action registry — canned data fetches that bypass the LLM.

Each entry describes a single hard-coded recipe: run one data tool with
static args, feed its result through the matching widget builder, persist
+ stream the widget. Zero LLM calls.

The "message" field is the text we save as the user message so the chat
history reads naturally ("See credit score" shows up just like typing it).

Only actions whose answer is deterministic + pure-data belong here.
Interactive sub-agent flows (transfer, refund) still need the Planner and
should NOT be quick-action-ified.
"""

from app.widgets.catalog import WIDGET_CATALOG

# action_id → {
#   "message":          str,   # text persisted as the user's message
#   "tool":             str,   # data-tool name (must have output_var)
#   "args":             dict,  # tool input
#   "widget_type":      str,   # catalog key; builder + slot_arg_map taken from catalog
#   "created_by":       str,   # WidgetInstance.created_by
# }
QUICK_ACTIONS: dict[str, dict] = {
    "recent_transactions": {
        "message": "What are my recent transactions?",
        "tool": "get_transactions_data",
        "args": {"view": "recent"},
        "widget_type": "transaction_list",
        "created_by": "quick_action:recent_transactions",
    },
    "account_balances": {
        "message": "Show me my account balances",
        "tool": "get_accounts_data",
        "args": {},
        "widget_type": "account_summary",
        "created_by": "quick_action:account_balances",
    },
    "credit_score": {
        "message": "See credit score",
        "tool": "get_profile_data",
        "args": {},
        "widget_type": "profile_card",
        "created_by": "quick_action:credit_score",
    },
}


def get_action(action_id: str) -> dict | None:
    return QUICK_ACTIONS.get(action_id)


def build_widget_for_action(action_id: str, slot_data) -> dict:
    """Call the catalog builder for the action's widget, feeding slot_data
    through slot_arg_map so we don't hard-code builder signatures here.

    Returns the widget dict (parsed JSON).
    """
    import json as _json

    action = QUICK_ACTIONS[action_id]
    widget_type = action["widget_type"]
    entry = WIDGET_CATALOG[widget_type]
    slot_arg_map = entry["slot_arg_map"]
    # slot_arg_map is {slot_name: builder_kwarg}. Quick actions are 1-slot,
    # so pass the tool's slot_data under the mapped kwarg.
    # The slot_name must match the tool's output_var by construction.
    kwarg = next(iter(slot_arg_map.values()))
    widget_json = entry["render_fn"](**{kwarg: slot_data})
    return _json.loads(widget_json)

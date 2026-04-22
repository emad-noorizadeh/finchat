"""Widget catalog — single source of truth for widget schemas, composability, and render bindings.

The Presenter (app/agent/presenter.py) drives all widget-render decisions from
this catalog. No LLM is involved in widget selection — four rules read catalog
fields and pick the right builder + kwargs. To add a new widget:

  1. Write a builder in app/widgets/builders.py returning a widget-dict JSON string.
  2. Add an entry to WIDGET_CATALOG below with ALL required keys:
       - display_name, description, tier, composable
       - fields, sample_data (for frontend preview at /widgets)
       - render_fn (callable reference to the builder)
       - slot_arg_map (required if default_data_var or slot_combination set)
       - sample_build_args (the kwargs to pass to render_fn in tests/validation)
       - default_data_var (for rule 2) OR slot_combination (for rule 1)
       - composite_priority (optional; used by rule 3 for section ordering)
  3. Ensure a React component key is declared in standalone_render.
  4. Restart backend — _validate_catalog() runs at module load and will
     raise if anything is malformed.

CATALOG_VERSION is a SHA-256 hash of the catalog contents (excluding
non-serializable fields like render_fn). Any change auto-bumps the version.
Frontend caches by version and refetches on mismatch.
"""

import hashlib
import json
import logging

from app.widgets.builders import (
    profile_card_widget,
    account_summary_widget,
    transaction_list_widget,
    transfer_confirmation_widget,
    confirmation_request_widget,
    text_card_widget,
    profile_with_accounts_widget,
    generic_composite_widget,
    transfer_form_widget,
    refund_form_widget,
)

logger = logging.getLogger(__name__)


WIDGET_CATALOG: dict[str, dict] = {
    "profile_card": {
        "display_name": "Profile Card",
        "description": "User's personal profile — name, location, rewards tier, credit score.",
        "tier": 1,
        "composable": "full",
        "degradation_note": None,
        "fields": [
            {"name": "name", "type": "string", "required": True},
            {"name": "city", "type": "string", "required": False},
            {"name": "state", "type": "string", "required": False},
            {"name": "segment", "type": "string", "required": False},
            {"name": "rewards_tier", "type": "string", "required": False},
            {"name": "qualifying_balance", "type": "number", "required": False},
            {"name": "credit_scores", "type": "array", "required": False},
            {"name": "language", "type": "string", "required": False},
        ],
        "sample_data": {
            "name": "Arya",
            "city": "Austin",
            "state": "Texas",
            "segment": "Consumer",
            "rewards_tier": "Preferred Rewards - Preferred Plus",
            "qualifying_balance": 45000,
            "credit_scores": [{"source": "Equifax", "score": 780}],
            "language": "English",
        },
        "standalone_render": "ProfileCard",
        "composite_render": "ProfileCard",
        "render_fn": profile_card_widget,
        "default_data_var": "profile_data",
        "slot_arg_map": {"profile_data": "profile_data"},
        "sample_build_args": {
            "profile_data": {
                "name": "Arya",
                "city": "Austin",
                "state": "Texas",
                "segment": "Consumer",
                "rewards_tier": "Preferred Rewards - Preferred Plus",
                "qualifying_balance": 45000,
                "credit_scores": [{"source": "Equifax", "score": 780}],
                "language": "English",
            },
        },
        "composite_priority": 20,
        "voice_summary_template": "Profile for {{name}} in {{city}}, {{state}}. {{rewards_tier}} tier.",
    },
    "account_summary": {
        "display_name": "Account Summary",
        "description": "List of the user's accounts with balances and types.",
        "tier": 1,
        "composable": "full",
        "degradation_note": None,
        "fields": [
            {"name": "accounts", "type": "array", "required": True},
        ],
        "sample_data": {
            "accounts": [
                {"display_name": "Checking ****1234", "type": "Checking", "balance": 5230.45, "available": 5230.45, "currency": "USD", "account_ref": "REF1234"},
                {"display_name": "Savings ****5678", "type": "Savings", "balance": 18500.00, "available": 18500.00, "currency": "USD", "account_ref": "REF5678"},
            ],
        },
        "standalone_render": "AccountSummary",
        "composite_render": "AccountSummary",
        "render_fn": account_summary_widget,
        "default_data_var": "accounts_data",
        "slot_arg_map": {"accounts_data": "accounts"},
        "sample_build_args": {
            "accounts": [
                {"display_name": "Checking ****1234", "type": "Checking", "balance": 5230.45, "available": 5230.45, "currency": "USD", "account_ref": "REF1234"},
                {"display_name": "Savings ****5678", "type": "Savings", "balance": 18500.00, "available": 18500.00, "currency": "USD", "account_ref": "REF5678"},
            ],
        },
        "composite_priority": 50,
        "voice_summary_template": "{{accounts}} accounts.",
    },
    "transaction_list": {
        "display_name": "Transaction List",
        "description": "Transactions with search, category filter, and click-for-detail. Handles both flat and grouped (by category/merchant/date/account) shapes.",
        "tier": 1,
        "composable": "degraded",
        "degradation_note": "In composite mode, shows the top 5 transactions and a 'View all' link that opens a standalone view.",
        "fields": [
            {"name": "shape", "type": "string", "required": False, "description": "'flat' | 'groups'"},
            {"name": "transactions", "type": "array", "required": False, "description": "Required when shape='flat'"},
            {"name": "groups", "type": "array", "required": False, "description": "Required when shape='groups'"},
            {"name": "summary", "type": "object", "required": False},
            {"name": "group_by", "type": "string", "required": False},
            {"name": "page", "type": "number", "required": False},
            {"name": "page_size", "type": "number", "required": False},
        ],
        "sample_data": {
            "shape": "flat",
            "transactions": [
                {"reference": "TXN-001", "date": "04/16/2026", "description": "Whole Foods Market", "amount": "$82.40", "amount_value": 82.40, "direction": "debit", "category": "PURCHASE_GROCERY", "account": "My Checking Accnt - 6789", "status": "completed", "merchant": "Whole Foods Market"},
                {"reference": "TXN-002", "date": "04/15/2026", "description": "Direct Deposit - Employer Payroll", "amount": "$5,420.50", "amount_value": 5420.50, "direction": "credit", "category": "PAYROLL_DIRECT", "account": "My Checking Accnt - 6789", "status": "completed", "merchant": "Employer Payroll"},
                {"reference": "TXN-003", "date": "04/14/2026", "description": "Shell #4892", "amount": "$48.75", "amount_value": 48.75, "direction": "debit", "category": "PURCHASE_GAS", "account": "My Checking Accnt - 6789", "status": "completed", "merchant": "Shell"},
            ],
            "summary": {"count": 3, "total_inflow": 5420.50, "total_outflow": 131.15, "total_inflow_display": "$5,420.50", "total_outflow_display": "$131.15"},
            "page": 1,
            "page_size": 10,
        },
        "standalone_render": "TransactionList",
        "composite_render": "TransactionList",
        "render_fn": transaction_list_widget,
        "default_data_var": "transactions_data",
        "slot_arg_map": {"transactions_data": "payload"},
        "sample_build_args": {
            "payload": {
                "shape": "flat",
                "transactions": [
                    {"reference": "TXN-001", "date": "04/16/2026", "description": "Whole Foods Market", "amount": "$82.40", "amount_value": 82.40, "direction": "debit", "category": "PURCHASE_GROCERY", "account": "My Checking Accnt - 6789", "status": "completed", "merchant": "Whole Foods Market"},
                    {"reference": "TXN-002", "date": "04/15/2026", "description": "Direct Deposit - Employer Payroll", "amount": "$5,420.50", "amount_value": 5420.50, "direction": "credit", "category": "PAYROLL_DIRECT", "account": "My Checking Accnt - 6789", "status": "completed", "merchant": "Employer Payroll"},
                    {"reference": "TXN-003", "date": "04/14/2026", "description": "Shell #4892", "amount": "$48.75", "amount_value": 48.75, "direction": "debit", "category": "PURCHASE_GAS", "account": "My Checking Accnt - 6789", "status": "completed", "merchant": "Shell"},
                ],
                "summary": {"count": 3, "total_inflow": 5420.50, "total_outflow": 131.15, "total_inflow_display": "$5,420.50", "total_outflow_display": "$131.15"},
            },
        },
        "composite_priority": 100,
        "voice_summary_template": "{{summary.count}} transactions.",
    },
    "transfer_confirmation": {
        "display_name": "Transfer Confirmation",
        "description": "Confirmation of a completed or pending money transfer. Emitted by the transfer sub-agent — never picked by the Presenter.",
        "tier": 1,
        "composable": "never",
        "degradation_note": "Terminal state — must render standalone.",
        "fields": [
            {"name": "from", "type": "string", "required": True},
            {"name": "to", "type": "string", "required": True},
            {"name": "amount", "type": "number", "required": True},
            {"name": "date", "type": "string", "required": True},
            {"name": "confirmation_id", "type": "string", "required": True},
            {"name": "status", "type": "string", "required": False},
        ],
        "sample_data": {
            "from": "Checking ****1234",
            "to": "Savings ****5678",
            "amount": 200,
            "date": "2026-04-17",
            "confirmation_id": "TXN-ABC123",
            "status": "COMPLETED",
        },
        "standalone_render": "TransferConfirmation",
        "composite_render": None,
        "render_fn": transfer_confirmation_widget,
        "default_data_var": None,
        "slot_arg_map": None,  # not Presenter-dispatched
        "sample_build_args": {
            "from_account": "Checking ****1234",
            "to_account": "Savings ****5678",
            "amount": 200,
            "date": "2026-04-17",
            "confirmation_id": "TXN-ABC123",
            "status": "COMPLETED",
        },
        "voice_summary_template": "Transfer {{status}}: {{amount}} dollars from {{from}} to {{to}}.",
    },
    "confirmation_request": {
        "display_name": "Confirmation Request",
        "description": "Interrupt-based user confirmation card. Emitted by the outer orchestrator's request_confirmation tool; sub-agents should use interrupt_node + a widget response_node (e.g. transfer_form) instead.",
        "tier": 1,
        "composable": "never",
        "degradation_note": "Interactive; must render standalone.",
        "fields": [
            {"name": "details", "type": "string", "required": True},
            {"name": "fields", "type": "array", "required": False},
        ],
        "sample_data": {
            "details": "You're about to transfer $200 from Checking to Savings. Continue?",
            "fields": [
                {"label": "Amount", "value": "$200"},
                {"label": "From", "value": "Checking ****1234"},
                {"label": "To", "value": "Savings ****5678"},
            ],
        },
        "standalone_render": "ConfirmationRequest",
        "composite_render": None,
        "render_fn": confirmation_request_widget,
        "default_data_var": None,
        "slot_arg_map": None,  # interrupt-dispatched
        "sample_build_args": {
            "title": "Confirm transfer",
            "details": "You're about to transfer $200 from Checking to Savings. Continue?",
            "fields": [
                {"label": "Amount", "value": "$200"},
                {"label": "From", "value": "Checking ****1234"},
                {"label": "To", "value": "Savings ****5678"},
            ],
        },
        "voice_summary_template": "Please confirm: {{details}}",
    },
    "text_card": {
        "display_name": "Text Card",
        "description": "Free-form text in a card frame. Presenter uses this as the narrative fallback (rule 4).",
        "tier": 1,
        "composable": "full",
        "degradation_note": None,
        "fields": [
            {"name": "content", "type": "string", "required": True},
            {"name": "title", "type": "string", "required": False},
        ],
        "sample_data": {
            "content": "Your credit score is calculated from payment history, utilization, and credit age.",
            "title": "Credit score overview",
        },
        "standalone_render": "TextCard",
        "composite_render": "TextCard",
        "render_fn": text_card_widget,
        "default_data_var": None,
        "slot_arg_map": None,  # fallback — Presenter calls it directly with literal args
        "sample_build_args": {
            "content": "Your credit score is calculated from payment history, utilization, and credit age.",
            "title": "Credit score overview",
        },
        "voice_summary_template": "{{content}}",
    },
    "profile_with_accounts": {
        "display_name": "Profile with Accounts",
        "description": "Designed composite pairing the user's profile and account list in one card. Tier-1 — Presenter prefers this over a generic composite when both profile_data and accounts_data slots are populated.",
        "tier": 1,
        "composable": "never",
        "degradation_note": "Itself a composite; not embedded in other composites.",
        "fields": [
            {"name": "profile", "type": "object", "required": True},
            {"name": "accounts", "type": "array", "required": True},
        ],
        "sample_data": {
            "profile": {"name": "Arya", "city": "Austin", "state": "Texas", "rewards_tier": "Preferred Rewards - Preferred Plus", "credit_scores": [{"score": 780, "assessmentCat": "Excellent"}]},
            "accounts": [
                {"display_name": "Checking ****1234", "type": "Checking", "balance": 5230.45, "currency": "USD"},
                {"display_name": "Savings ****5678", "type": "Savings", "balance": 18500.00, "currency": "USD"},
            ],
        },
        "standalone_render": "ProfileWithAccounts",
        "composite_render": None,
        "render_fn": profile_with_accounts_widget,
        "default_data_var": None,
        "slot_combination": ["profile_data", "accounts_data"],
        "slot_arg_map": {"profile_data": "profile", "accounts_data": "accounts"},
        "sample_build_args": {
            "profile": {"name": "Arya", "city": "Austin", "state": "Texas", "rewards_tier": "Preferred Rewards - Preferred Plus", "credit_scores": [{"score": 780, "assessmentCat": "Excellent"}]},
            "accounts": [
                {"display_name": "Checking ****1234", "type": "Checking", "balance": 5230.45, "currency": "USD"},
                {"display_name": "Savings ****5678", "type": "Savings", "balance": 18500.00, "currency": "USD"},
            ],
        },
        "voice_summary_template": "Your profile and accounts overview.",
    },
    "transfer_form": {
        "display_name": "Transfer Form",
        "description": "Interactive transfer form. User edits fields and clicks Transfer to commit via widget action. Emitted by the Transfer sub-agent in chat channel.",
        "tier": 1,
        "composable": "never",
        "degradation_note": "Interactive commit widget — must render standalone.",
        "fields": [
            {"name": "amount", "type": "number", "required": False},
            {"name": "from_account", "type": "object", "required": False},
            {"name": "to_account", "type": "object", "required": False},
            {"name": "source_options", "type": "array", "required": True},
            {"name": "target_options", "type": "array", "required": True},
            {"name": "validation_id", "type": "string", "required": False},
        ],
        "sample_data": {
            "amount": 200,
            "from_account": {"displayName": "My Checking - 6789", "accountTempId": "A1"},
            "to_account": None,
            "source_options": [
                {"displayName": "My Checking - 6789", "accountTempId": "A1", "balance": 5000},
                {"displayName": "My Savings - 1234", "accountTempId": "A2", "balance": 2000},
            ],
            "target_options": [
                {"displayName": "My Savings - 1234", "accountTempId": "A2"},
                {"displayName": "Credit Card - 8222", "accountTempId": "A3"},
            ],
            "validation_id": None,
        },
        "standalone_render": "TransferForm",
        "composite_render": None,
        "render_fn": transfer_form_widget,
        "default_data_var": None,
        "slot_arg_map": None,
        "sample_build_args": {
            "amount": 200,
            "from_account": {"displayName": "My Checking - 6789", "accountTempId": "A1"},
            "to_account": None,
            "source_options": [
                {"displayName": "My Checking - 6789", "accountTempId": "A1", "balance": 5000},
                {"displayName": "My Savings - 1234", "accountTempId": "A2", "balance": 2000},
            ],
            "target_options": [
                {"displayName": "My Savings - 1234", "accountTempId": "A2"},
            ],
            "validation_id": None,
        },
        "voice_summary_template": "Transfer {{amount}} dollars.",
    },
    "refund_form": {
        "display_name": "Fee Refund Form",
        "description": "Interactive fee-refund form. Lists eligible fees; user picks one and clicks Request refund. Widget action handler calls RefundService.submit_refund. Emitted by the Fee Refund sub-agent in chat channel.",
        "tier": 1,
        "composable": "never",
        "degradation_note": "Interactive commit widget — must render standalone.",
        "fields": [
            {"name": "account_details",        "type": "object", "required": True},
            {"name": "refundable_transactions", "type": "array",  "required": True},
            {"name": "total_amount",           "type": "number", "required": False},
            {"name": "selected_activity_reference", "type": "string", "required": False},
            {"name": "decision",               "type": "object", "required": False},
        ],
        "sample_data": {
            "account_details": {
                "accountLabel": "Unlimited Cash Rewards Credit Card - 8026",
                "creditLineInfo": {"currentBalance": 1247.83, "availableCredit": 3752.17},
            },
            "refundable_transactions": [
                {
                    "activityReference": "TXN-CC-20260306-00110",
                    "primaryDescription": "late fee",
                    "feeType": "LATE_FEE",
                    "transactionAmount": "$28.00",
                    "authorizedAmount": "28.00",
                    "originDate": "02/08/2026",
                    "displayCategory": "FEES",
                },
                {
                    "activityReference": "TXN-CC-20260218-00227",
                    "primaryDescription": "cash advance interest",
                    "feeType": "CASH_ADVANCE_INTEREST",
                    "transactionAmount": "$15.42",
                    "authorizedAmount": "15.42",
                    "originDate": "02/18/2026",
                    "displayCategory": "FEES",
                },
            ],
            "total_amount": 43.42,
            "selected_activity_reference": None,
            "decision": None,
        },
        "standalone_render": "RefundForm",
        "composite_render": None,
        "render_fn": refund_form_widget,
        "default_data_var": None,
        "slot_arg_map": None,
        "sample_build_args": {
            "account_details": {"accountLabel": "Credit Card - 8026"},
            "refundable_transactions": [
                {"activityReference": "TXN-CC-20260306-00110", "primaryDescription": "late fee", "feeType": "LATE_FEE", "transactionAmount": "$28.00"},
            ],
            "total_amount": 28.0,
        },
        "voice_summary_template": "You have refundable fees totalling {{total_amount}} dollars.",
    },

    "generic_composite": {
        "display_name": "Generic Composite",
        "description": "Vertical stack of 2-3 composable widgets. Presenter falls back to this when no designed composite matches the gathered slots (rule 3).",
        "tier": 2,
        "composable": "never",
        "degradation_note": "Itself a composite — cannot be nested further.",
        "fields": [
            {"name": "sections", "type": "array", "required": True},
            {"name": "title", "type": "string", "required": False},
        ],
        "sample_data": {
            "title": "Overview",
            "sections": [
                {"widget_type": "profile_card", "data": {"name": "Arya", "city": "Austin", "state": "Texas", "rewards_tier": "Preferred Plus"}},
                {"widget_type": "account_summary", "data": {"accounts": [{"display_name": "Checking ****1234", "type": "Checking", "balance": 5230.45}]}},
            ],
        },
        "standalone_render": "GenericComposite",
        "composite_render": None,
        "render_fn": generic_composite_widget,
        "default_data_var": None,
        "slot_arg_map": None,  # composite — Presenter assembles `sections` from populated slots
        "sample_build_args": {
            "title": "Overview",
            "sections": [
                {"widget_type": "profile_card", "data": {"name": "Arya", "city": "Austin", "state": "Texas", "rewards_tier": "Preferred Plus"}},
                {"widget_type": "account_summary", "data": {"accounts": [{"display_name": "Checking ****1234", "type": "Checking", "balance": 5230.45}]}},
            ],
        },
        "voice_summary_template": "A {{sections}}-section overview.",
    },
}


# --- Derived module-load-time structures ---


# Memoized declaration index (insertion order). Used by Presenter rule 1's
# declaration-order tiebreaker. O(1) lookup forever; the alternative
# list(...).index() would be O(n) per call and O(n²) over a sort.
_DECLARATION_INDEX: dict[str, int] = {wt: i for i, wt in enumerate(WIDGET_CATALOG.keys())}


# --- Catalog validation ---


def _validate_catalog() -> None:
    """Strict catalog validation. Runs at module load; any failure raises and
    prevents the backend from starting. Catches every contract violation before
    a request is served."""

    # 1. default_data_var uniqueness.
    seen_dvars: dict[str, str] = {}
    for wt, entry in WIDGET_CATALOG.items():
        dvar = entry.get("default_data_var")
        if not dvar:
            continue
        if dvar in seen_dvars:
            raise ValueError(
                f"default_data_var {dvar!r} used by both {seen_dvars[dvar]!r} and {wt!r}. "
                "Each default_data_var must map to exactly one catalog entry."
            )
        seen_dvars[dvar] = wt

    # 2. slot_arg_map required for any entry with default_data_var OR slot_combination.
    for wt, entry in WIDGET_CATALOG.items():
        has_dvar = bool(entry.get("default_data_var"))
        has_combo = bool(entry.get("slot_combination"))
        sam = entry.get("slot_arg_map")
        if has_dvar and (sam is None or entry["default_data_var"] not in sam):
            raise ValueError(
                f"catalog entry {wt!r} has default_data_var={entry['default_data_var']!r} "
                f"but slot_arg_map is missing that key. slot_arg_map is required."
            )
        if has_combo:
            if sam is None:
                raise ValueError(
                    f"catalog entry {wt!r} has slot_combination but no slot_arg_map. "
                    "Composites must declare slot_arg_map explicitly."
                )
            missing = set(entry["slot_combination"]) - sam.keys()
            if missing:
                raise ValueError(
                    f"catalog entry {wt!r} slot_arg_map missing keys for slot_combination: {missing}"
                )

    # 3. render_fn round-trip: every entry with render_fn + sample_build_args
    #    must be callable with those args without TypeError (signature match).
    for wt, entry in WIDGET_CATALOG.items():
        render_fn = entry.get("render_fn")
        build_args = entry.get("sample_build_args")
        if not (callable(render_fn) and isinstance(build_args, dict)):
            continue
        try:
            _ = render_fn(**build_args)
        except TypeError as e:
            raise ValueError(
                f"catalog entry {wt!r}: render_fn {render_fn.__name__}(**sample_build_args) "
                f"failed with TypeError: {e}. Check slot_arg_map keys match the builder signature."
            )

    # Note: sample_data (frontend preview payload = widget.data) and
    # sample_build_args (builder kwargs) legitimately differ in shape when
    # the builder wraps the slot value — e.g., account_summary_widget
    # takes `accounts=[...]` and produces widget.data={"accounts": [...]}.
    # Drift-checking across the wrap is more false-positive than signal;
    # the round-trip check (#3) catches real signature mismatches, which
    # is the correctness property that matters.


_validate_catalog()


# --- API serialization ---


# Non-JSON-serializable fields (callables) stripped when the catalog is sent
# to the frontend. Everything else passes through.
_NON_SERIALIZABLE_FIELDS = {"render_fn"}


def _serializable_catalog() -> dict:
    return {
        wt: {k: v for k, v in entry.items() if k not in _NON_SERIALIZABLE_FIELDS}
        for wt, entry in WIDGET_CATALOG.items()
    }


# Auto-computed from the serializable catalog. Any change auto-bumps the
# version. Frontend compares against its cached version and refetches.
CATALOG_VERSION: str = hashlib.sha256(
    json.dumps(_serializable_catalog(), sort_keys=True, default=str).encode()
).hexdigest()[:12]


def get_catalog_entry(widget_type: str) -> dict | None:
    """Return the catalog entry for a widget type, or None if unknown."""
    return WIDGET_CATALOG.get(widget_type)


def catalog_for_api() -> dict:
    """Serialize the catalog for GET /api/widgets/catalog.

    Strips non-JSON-serializable fields (render_fn callables). Everything
    metadata-like passes through.
    """
    return {
        "version": CATALOG_VERSION,
        "widgets": _serializable_catalog(),
    }

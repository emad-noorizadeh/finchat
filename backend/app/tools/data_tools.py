"""Data tools — gather structured JSON into state.variables for render tools to consume.

Contract (invariant 1):
- Data tools NEVER emit widgets. They return ToolResult(to_llm=json.dumps(...)) only.
- Each declares output_var so tool_execute writes the parsed JSON to state.variables[output_var].
- Render tools in app/tools/render_tools.py consume those slots via <field>_slot args.
"""

import json

from app.tools.base import BaseTool, ToolResult
from app.tools import register_tool


class GetProfileDataTool(BaseTool):
    name = "get_profile_data"
    always_load = True
    should_defer = False
    channels = ("chat", "voice")
    search_hint = "user profile name rewards tier credit score"
    is_read_only = True
    output_var = "profile_data"
    flow = (
        "Fetch user profile from in-memory store",
        "Extract name, address, rewards tier, credit scores",
        "Return JSON to state.variables.profile_data (no widget, no termination)",
    )

    async def description(self, context=None):
        return (
            "Fetch the user's personal profile as structured data (name, location, "
            "rewards tier, credit score). Returns JSON into the profile_data slot; "
            "does NOT emit a widget itself. Pair with present_widget() to render.\n\n"
            "Examples of when to call this tool:\n"
            "- User: \"Who am I?\" → get_profile_data()\n"
            "- User: \"What's my rewards tier?\" → get_profile_data()\n"
            "- User: \"Show my credit score\" → get_profile_data()"
        )

    async def input_schema(self):
        return {"type": "object", "properties": {}}

    def activity_description(self, input):
        return "Looking up profile..."

    async def execute(self, input: dict, context: dict) -> ToolResult:
        from app.services.profile_service import get_profile

        user_id = context.get("user_id", "")
        profile = get_profile(user_id)
        if not profile:
            return ToolResult(to_llm=json.dumps({"error": "Profile not loaded."}))

        name_info = profile.get("profileName", {})
        address = profile.get("mailingAddress", {})
        rewards = profile.get("rewardsProfile", {})
        scores = profile.get("scoreDetails", [])

        profile_data = {
            "name": name_info.get("userName") or name_info.get("firstName", ""),
            "city": address.get("city", ""),
            "state": address.get("state", {}).get("value", ""),
            "segment": profile.get("businessSegment", {}).get("name", ""),
            "rewards_tier": rewards.get("tierDisplayName") or "Standard",
            "qualifying_balance": rewards.get("qualifyingBalance", 0),
            "credit_scores": scores[:3] if scores else [],
            "language": profile.get("userLanguagePref", {}).get("value", ""),
        }
        return ToolResult(to_llm=json.dumps(profile_data))


class GetAccountsDataTool(BaseTool):
    name = "get_accounts_data"
    always_load = False
    should_defer = True
    channels = ("chat", "voice")
    search_hint = "account balance type checking savings credit"
    is_read_only = True
    output_var = "accounts_data"

    async def description(self, context=None):
        return (
            "Fetch the user's accounts as a structured list (display name, type, "
            "balance, available). Returns JSON into the accounts_data slot; does "
            "NOT emit a widget itself. Pair with present_widget() to render.\n\n"
            "Examples of when to call this tool:\n"
            "- User: \"What's my checking balance?\" → get_accounts_data()\n"
            "- User: \"Show me my accounts\" → get_accounts_data()\n"
            "- User: \"How much do I have in savings?\" → get_accounts_data()"
        )

    async def input_schema(self):
        return {"type": "object", "properties": {}}

    def activity_description(self, input):
        return "Fetching account details..."

    async def execute(self, input: dict, context: dict) -> ToolResult:
        from app.services.profile_service import get_accounts

        user_id = context.get("user_id", "")
        accounts = get_accounts(user_id)
        if not accounts:
            return ToolResult(to_llm=json.dumps({"error": "No accounts found."}))

        result = []
        for acct in accounts:
            result.append({
                "display_name": acct.get("displayName", ""),
                "type": acct.get("identityDetails", {}).get("type", {}).get("longDescription", ""),
                "variant": acct.get("identityDetails", {}).get("offeringVariant", ""),
                "balance": acct.get("currentBalInfo", {}).get("amt", 0),
                "available": acct.get("availableBalInfo", {}).get("amt", 0),
                "currency": acct.get("currentBalInfo", {}).get("currency", {}).get("code", "USD"),
                "account_ref": acct.get("displayAccountReference", ""),
            })
        return ToolResult(to_llm=json.dumps(result))


class GetTransactionsDataTool(BaseTool):
    name = "get_transactions_data"
    # Transactions are a core banking-assistant capability — keeping this
    # always-loaded means "show my transactions" hits the fast-path directly
    # (get_transactions_data + present_widget in a single Planner turn) instead
    # of paying for an extra tool_search round-trip.
    always_load = True
    should_defer = False
    channels = ("chat", "voice")
    search_hint = "transactions payments history search recent by date merchant category"
    is_read_only = True
    output_var = "transactions_data"

    async def description(self, context=None):
        return (
            "Fetch and analyze the user's transactions. All views route through "
            "a shared analyzer supporting filter + group + sort + limit.\n\n"
            "Views (shortcuts for common intents):\n"
            "- \"recent\"       — flat list sorted by date (descending)\n"
            "- \"search\"       — flat list filtered by `query` substring\n"
            "- \"by_category\"  — grouped by displayCategory (PURCHASE_GAS, PURCHASE_GROCERY, …)\n"
            "- \"by_merchant\"  — grouped by derived merchant name\n"
            "- \"by_date\"      — grouped by settlement date\n"
            "- \"by_account\"   — grouped by linked account\n\n"
            "Any view accepts optional filter params: `query`, `category`, "
            "`direction` (credit/debit), `account`, `date_from`, `date_to`, "
            "`min_amount`, `max_amount`. Grouped views order groups by total "
            "amount descending. `limit` caps rows (or groups when grouped).\n\n"
            "Returns JSON into the transactions_data slot; does NOT emit a widget. "
            "Pair with present_widget() for visual output.\n\n"
            "Examples:\n"
            "- \"Show my transactions by category\" → get_transactions_data(view=\"by_category\")\n"
            "- \"How much did I spend on groceries?\" → get_transactions_data(view=\"search\", category=\"PURCHASE_GROCERY\")\n"
            "- \"Show transactions over $100 last month\" → "
            "get_transactions_data(view=\"recent\", min_amount=100, date_from=\"03/01/2026\")\n"
            "- \"What did I spend at Amazon?\" → get_transactions_data(view=\"search\", query=\"Amazon\")\n"
            "- \"Show my recent transactions\" → get_transactions_data(view=\"recent\")\n"
            "- \"Where do I spend most?\" → get_transactions_data(view=\"by_merchant\")"
        )

    async def input_schema(self):
        return {
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "enum": [
                        "recent", "search",
                        "by_category", "by_merchant", "by_date", "by_account",
                    ],
                },
                "query": {"type": "string", "default": ""},
                "category": {"type": "string", "default": ""},
                "direction": {"type": "string", "enum": ["", "credit", "debit"], "default": ""},
                "account": {"type": "string", "default": ""},
                "date_from": {"type": "string", "default": "", "description": "MM/DD/YYYY"},
                "date_to": {"type": "string", "default": "", "description": "MM/DD/YYYY"},
                "min_amount": {"type": "number"},
                "max_amount": {"type": "number"},
                "limit": {
                    "type": "integer",
                    "description": "Max rows returned. Omit (or 0) to return all — the widget paginates client-side so all rows let the user search and filter over the full dataset.",
                },
            },
            "required": ["view"],
        }

    def activity_description(self, input):
        return f"Loading transactions ({input.get('view', '')})..."

    async def execute(self, input: dict, context: dict) -> ToolResult:
        from app.services import transaction_service as ts
        from app.services.transaction_analyzer import TxnQuery, analyze

        user_id = context.get("user_id", "")
        records = ts.get_transaction_records(user_id)
        if not records:
            empty = {
                "shape": "flat", "transactions": [], "total": 0,
                "summary": {"count": 0}, "error": "No transactions loaded for this user.",
            }
            return ToolResult(to_llm=json.dumps(empty), slot_data=empty)

        view = input.get("view", "recent")
        group_map = {
            "by_category": "category",
            "by_merchant": "merchant",
            "by_date": "date",
            "by_account": "account",
        }

        if view == "search" and not (input.get("query") or input.get("category")):
            err = {
                "shape": "flat", "transactions": [], "total": 0,
                "summary": {"count": 0},
                "error": "query or category is required for view='search'",
            }
            return ToolResult(to_llm=json.dumps(err), slot_data=err)

        # Limit policy: widget paginates and filters client-side, so the tool
        # returns all rows by default. The LLM can still cap explicitly (e.g.
        # a search view with thousands of hits) by passing a positive limit.
        raw_limit = input.get("limit")
        limit = int(raw_limit) if raw_limit and int(raw_limit) > 0 else None

        query = TxnQuery(
            query=input.get("query", ""),
            category=input.get("category", ""),
            direction=input.get("direction", ""),
            account=input.get("account", ""),
            date_from=input.get("date_from", ""),
            date_to=input.get("date_to", ""),
            min_amount=input.get("min_amount"),
            max_amount=input.get("max_amount"),
            group_by=group_map.get(view, ""),
            sort="date_desc",
            limit=limit,
        )

        result = analyze(records, query)
        return ToolResult(
            to_llm=_summary_for_llm(result),
            slot_data=result,
        )


def _summary_for_llm(result: dict) -> str:
    """Compact summary sent to the LLM. Full data stays in the slot for the widget.

    Shape:
        Flat   → {"shape": "flat", "count": N, "summary": {...}, "sample": [top 3 rows, trimmed]}
        Groups → {"shape": "groups", "group_by": X, "count": N_txns, "groups": [{group, count, total}, ...]}

    This is ~10× smaller than the full render payload and gives the LLM
    enough to reason about (call present_widget, describe to the user,
    decide whether to drill in).
    """
    summary = result.get("summary") or {}
    if result.get("shape") == "groups":
        groups = result.get("groups") or []
        return json.dumps({
            "shape": "groups",
            "group_by": result.get("group_by", ""),
            "count": result.get("total", 0),
            "summary": summary,
            "groups": [
                {"group": g.get("group"), "count": g.get("count"),
                 "total": g.get("total_amount_display")}
                for g in groups
            ],
        })

    txns = result.get("transactions") or []
    sample = [
        {
            "date": t.get("date"),
            "description": t.get("description"),
            "amount": t.get("amount"),
            "direction": t.get("direction"),
            "category": t.get("category"),
        }
        for t in txns[:3]
    ]
    return json.dumps({
        "shape": "flat",
        "count": result.get("total", 0),
        "summary": summary,
        "sample": sample,
    })


register_tool(GetProfileDataTool())
register_tool(GetAccountsDataTool())
register_tool(GetTransactionsDataTool())

"""get_card_offer — returns a fixed catalogue of three credit-card offers.

Deferred (surfaced via tool_search) and read-only. Used by the card_offer
sub-agent template to suggest cards when a user asks about credit cards.
"""

import json

from app.tools.base import BaseTool, ToolResult
from app.tools import register_tool


_CARD_OFFERS = [
    {
        "name": "Globetrotter Travel Card",
        "category": "travel",
        "annual_fee": 95,
        "highlights": [
            "3x points on travel and dining",
            "No foreign transaction fees",
            "$200 travel credit per year",
        ],
        "best_for": "Frequent travelers who book flights and hotels often.",
    },
    {
        "name": "Everyday Cash Rewards",
        "category": "everyday",
        "annual_fee": 0,
        "highlights": [
            "1.5% cash back on every purchase",
            "No annual fee",
            "Easy redemption to checking",
        ],
        "best_for": "Anyone who wants simple, no-fee cash back on all spending.",
    },
    {
        "name": "Fuel Saver Card",
        "category": "gas_saver",
        "annual_fee": 0,
        "highlights": [
            "5% back at gas stations",
            "3% back on EV charging",
            "1% on all other purchases",
        ],
        "best_for": "Drivers who spend a lot on gas or EV charging each month.",
    },
]


class GetCardOfferTool(BaseTool):
    name = "get_card_offer"
    always_load = False
    should_defer = True
    channels = ("chat", "voice")
    search_hint = "credit card offer recommend suggest apply travel cash gas rewards"
    is_read_only = True
    output_var = "card_offers"

    async def description(self, context=None):
        return (
            "Return a catalogue of credit-card offers the user is eligible for. "
            "Returns JSON into the card_offers slot; the sub-agent / Presenter "
            "renders it. Three card types are always returned: a travel rewards "
            "card, a no-annual-fee everyday cash-back card, and a gas-saver card.\n\n"
            "Examples of when to call this tool:\n"
            "- User: \"I need a credit card\" → get_card_offer()\n"
            "- User: \"What card offers do you have?\" → get_card_offer()\n"
            "- User: \"Recommend a card for travel\" → get_card_offer()"
        )

    async def input_schema(self):
        return {"type": "object", "properties": {}}

    def activity_description(self, input):
        return "Looking up card offers..."

    async def execute(self, input: dict, context: dict) -> ToolResult:
        return ToolResult(to_llm=json.dumps({"offers": _CARD_OFFERS}))


register_tool(GetCardOfferTool())

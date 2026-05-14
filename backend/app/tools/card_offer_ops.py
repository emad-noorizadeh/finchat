"""CardOfferOpsTool — AgentTool for the card_offer sub-agent.

Single namespace (`card_offer`), one action (`list_offers`). The
`card_offer` sub-agent's tool_call_node addresses it as
`{tool: "card_offer", action: "list_offers"}`.

Offers are a fixed catalogue for demo purposes. In a real system this
would query the offers service / API.
"""

from __future__ import annotations

import logging

from app.tools.agent_tool import AgentTool, action, register_agent_tool

logger = logging.getLogger(__name__)


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


class CardOfferOpsTool(AgentTool):
    """Card-offer operations for the card_offer sub-agent."""

    name = "card_offer"
    # Global scope ("" = callable from any sub-agent, not just card_offer).
    # The catalogue is generic — browse and recommend agents both call it.
    agent_name = ""
    description = (
        "Card-offer operations. list_offers returns a catalogue of credit-card "
        "offers the user is eligible for."
    )
    scope = "sub_agent"

    @action(
        "list_offers",
        description=(
            "Return a catalogue of credit-card offers. Three options are always "
            "returned: a travel rewards card, a no-annual-fee everyday cash-back "
            "card, and a gas-saver card."
        ),
        params_schema={"type": "object", "properties": {}},
        output_schema={
            "type": "object",
            "properties": {
                "offers": {"type": "array"},
            },
        },
    )
    async def list_offers(self, params: dict, context: dict) -> dict:
        return {"offers": _CARD_OFFERS}


register_agent_tool(CardOfferOpsTool())

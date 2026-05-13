# Extending FinChat — Tools, Agents, and Widgets

Audience: engineers adding new capabilities to FinChat. This doc covers what you need to know to safely add a tool, add an agent (regulated or not), and understand how each surface gets exposed to the LLM Planner and the Agent Builder UI.

For background on the orchestrator graph and the Presenter engine, read [`architecture.md`](./architecture.md) and [`widgets.md`](./widgets.md) first.

---

## Mental model — the two registries

The single most important concept to internalize before you touch this code:

```
Tool registry      app/tools/__init__.py:_REGISTRY
   ↓
   This is what the LLM Planner sees.
   To be callable by the LLM, a BaseTool must live here.

Agent registry     app/agents/__init__.py:_AGENT_NAMES / _AGENT_CHANNELS / _AGENT_TEMPLATE
   ↓
   Metadata about templates that exist. Says "agent X has chat+voice variants."
   Does NOT make X callable on its own.
   Used by tool_search to filter ("hide tools whose agent has no template for this channel").
```

**A template alone is not callable.** It's a graph definition. The LLM only ever calls *tools*. To make a template reachable from the LLM, **something has to register a `BaseTool` whose `execute()` runs that template's graph.**

That "something" is one of:

- **`DynamicSubAgentTool`** — generic wrapper, auto-created for non-regulated templates by `refresh_dynamic_sub_agent_tools()`.
- **A hand-coded `BaseTool` subclass** — what `TransferAgentTool` and `RefundAgentTool` are.

Keep this in mind throughout the rest of the doc.

---

## Part 1 — Adding a Tool

### The contract — `BaseTool` (`app/tools/base.py`)

Every tool is a class inheriting `BaseTool`. The class attributes define metadata; the methods define behavior.

**Required:**

| Attribute / method | Purpose |
|---|---|
| `name` | Unique key in the registry. |
| `should_defer` *or* `always_load` | Exactly one must be `True`. `register_tool` raises if both/neither. |
| `async execute(input, context)` | The actual work; returns a `ToolResult`. |

**Highly recommended:**

| Attribute / method | Purpose |
|---|---|
| `async description(context)` | What the LLM sees. **This IS the model registration** — there's no separate step. |
| `async input_schema()` | JSON Schema for args. Becomes the OpenAI function-calling `parameters` block. |
| `search_hint` | Keywords for `tool_search` to match the deferred tool against user intent. |
| `channels` | `("chat",)` default, or `("chat", "voice")`. |
| `output_var` | If set, runtime auto-writes parsed `to_llm` JSON into `state.variables[output_var]`. How data tools feed render tools. |
| `widget` | Declared widget type. Used by the Presenter for slot routing. |
| `flow`, `validations`, `errors` | Self-describing metadata. Surfaced in `/api/tools` and the agent builder. |
| `is_read_only`, `is_concurrency_safe`, `is_internal` | Behavioral flags. `is_internal=True` hides the tool from the UI. |

### Two flavors — always-load vs. deferred

| Flavor | When the LLM sees it | Use for |
|---|---|---|
| `always_load=True` | Bound to every LLM turn. | High-frequency intents only — `get_profile_data`, `transfer_money`, `present_widget`. **Token cost on every turn — keep this set small.** |
| `should_defer=True` | Invisible until the Planner calls `tool_search` with a query. Top 5 weighted matches load for one turn. | Default for everything else. Adding deferred tools is essentially free in steady-state token cost. |

The "Claude Code pattern" — the deferred-tool loading mechanism — is what keeps the always-loaded catalogue small.

### Registering with the model — there is no separate step

The flow is:

1. Define the class.
2. Instantiate and call `register_tool(YourTool())` at the bottom of the module.
3. Add `from app.tools import your_module  # noqa` to `init_tools()` in `app/tools/__init__.py`.

That's it. On the next chat turn:

- Always-load tools are pulled in via `get_always_load_tools(channel)` (filtered by channel and agent-variant availability).
- Deferred tools are surfaced when `tool_search` matches them.

The bridge to the model is `BaseTool.to_openai_schema()` — it converts the tool's `name`, `description()`, and `input_schema()` into the OpenAI function-calling format. **The LLM never sees the Python — it only sees those three things.**

### What you need to internalize when adding a tool

- **Pick `always_load` vs `should_defer` deliberately.** Wrong choice = wasted tokens or invisible tool.
- **`description()` is the contract with the LLM.** It determines whether the tool gets called. The `transfer_money` description was recently expanded specifically to advertise all 3 transfer types so the Planner picks it. **Treat the description as code, review it like code.**
- **`search_hint` is the contract with the Planner.** Short keyword list, not prose. If the tool is deferred and never gets surfaced, this is usually why.
- **`output_var` + Presenter slots are the data flow.** Data tools write JSON to a slot; render tools read from slots. Don't build private side channels.
- **`ToolResult` failure contract is strict** (see `base.py:ToolErrorCategory` and the `ToolResult` docstring):
  - `error` is INTERNAL — logs only. May contain account numbers, technical detail.
  - `user_facing_message` is what the orchestrator LLM sees on failure. Must be safe to paraphrase — no slot values, no sensitive identifiers.
  - `error_category` (`POLICY` / `AUTH` / `VALIDATION` / `TRANSIENT` / `SYSTEM`) drives sub-agent error routing.

### Quick checklist — adding a tool

1. New file in `app/tools/`, define a `BaseTool` subclass.
2. Decide `should_defer` vs `always_load`.
3. Write `description()` (LLM-facing) and `search_hint` (Planner-facing) carefully.
4. Instantiate + `register_tool(instance)` at module bottom.
5. Add `from app.tools import your_module  # noqa` to `init_tools()` in `tools/__init__.py`.
6. Restart backend. The tool now shows in `/api/tools`, in the Agent Builder, and (if always_load) is bound to every turn.

### `BaseTool` vs `AgentTool` — when to use which

There are **two tool kinds** in the codebase. Picking the wrong one is the #1 reason a new tool "doesn't get called."

| | `BaseTool` (`app/tools/base.py`) | `AgentTool` (`app/tools/agent_tool.py`) |
|---|---|---|
| **Caller** | The main orchestrator's Planner LLM | A sub-agent's `tool_call_node` inside a template |
| **Discovery** | `tool_search` (deferred) or always-load list | The sub-agent template names it: `{tool: "X", action: "Y"}` |
| **Method** | `async def execute(input, context)` | `@action("name", ...) async def handler(self, params, context)` — one tool can declare multiple actions |
| **Registration** | `register_tool(YourTool())` | `register_agent_tool(YourTool())` |
| **Visible in `/api/tools`** | Yes (unless `is_internal=True`) | Yes, listed separately with declared actions |
| **Example** | `get_profile_data`, `knowledge_search`, `transfer_money` (entry tool) | `transfer` (`get_details`, `validate`, `submit`), `refund` (`list_fees`, `submit_refund`), `card_offer` (`list_offers`) |

**Rule of thumb:** if the Planner should be able to call it directly to answer a user → `BaseTool`. If it's a step inside a multi-step sub-agent flow → `AgentTool`.

You can have both for the same underlying capability. E.g., the Transfer flow:
- `TransferAgentTool(BaseTool)` is the Planner's entry — "transfer money" intent
- `TransferOpsTool(AgentTool)` exposes `get_details`, `validate`, `submit` actions that the sub-agent's graph nodes call

### Schema changes

If your tool / agent / model change touches any column in `backend/app/models/`, you must generate an alembic migration:

```bash
cd backend && source .venv/bin/activate
# Make your model change first, then:
alembic revision --autogenerate -m "describe the change"
```

Inspect the generated file in `backend/migrations/versions/`. **NOT NULL columns added to existing populated tables MUST have a `server_default`** — autogen sometimes forgets this; without it the ALTER will fail. Test locally:

```bash
./scripts/migrate.sh                     # apply
alembic downgrade -1                     # verify rollback works
./scripts/migrate.sh                     # re-apply
```

Commit the migration file alongside the model change. **Never edit a migration after it's been merged** — write a follow-up migration instead. The full migration / deploy story is in [`deploy_runbook.md`](./deploy_runbook.md).

---

## Part 2 — Adding an Agent (sub-agent)

The architecture moved away from class-based sub-agents. **Agents are now JSON templates that compile into LangGraph StateGraphs.**

### Where agents live

| Location | Role |
|---|---|
| `app/agents/templates/*.json` | File seeds. Imported on first boot against an empty DB. After seeding, the DB is the source of truth (with re-sync rules — see below). |
| `sub_agent_templates` table | Runtime source of truth. |
| `app/agents/patterns/*.json` | Starter skeletons (`collect_one_slot.json`, `confirm_then_execute.json`) — the Builder lets you clone these into a new graph. |

### Two ways to author an agent

#### Way A — the Agent Builder UI (the expected path for non-regulated agents)

Frontend: `frontend/src/components/agents/AgentBuilder.jsx` + `frontend/src/pages/AgentBuilderPage.jsx`. Goes through the `/api/agents` write endpoints in `app/routers/agents.py`. After a write, the router auto-calls `refresh_dynamic_sub_agent_tools()` which rebuilds the registry. **No backend restart needed.**

##### UI layout — what's where

- **Top bar** — agent name, status, **Save as Draft** / **Save & Deploy**.
- **Left panel (Settings)** — three tabs:
  - *General*: display name, slug, channel, **description** (LLM-facing), **search hint**.
  - *Prompt*: per-node prompt overview (read-only summary).
  - *Settings*: response format, read-only flag, require confirmation, **Always-load** checkbox.
- **Centre — graph canvas** — drag nodes around, drag from a node handle to draw an edge. Click `+` to add a node (parse / condition / interrupt / tool_call / llm / tool / response).
- **Right panel (Node Properties)** — appears when a node is selected. Every node type has its own editor (tool dropdown for `tool_call_node`, return-mode dropdown for `response_node`, etc.). Edits persist into form state as you go; the **Save** buttons at the top push everything to `/api/agents` in one request.

##### Step-by-step: build the `card_offer` agent

This walks through a real working agent. The agent calls a tool and returns the result for the orchestrator to paraphrase. Output: when the user says "show me your card offers," the chat returns three card suggestions.

**Step 1 — write the AgentTool** (the leaf that does the actual work).

Sub-agent `tool_call_node` calls **AgentTools** (not `BaseTool` — see "BaseTool vs AgentTool" in Part 1). Create `backend/app/tools/card_offer_ops.py`:

```python
from app.tools.agent_tool import AgentTool, action, register_agent_tool

_OFFERS = [
    {"name": "Globetrotter Travel Card", "category": "travel", "annual_fee": 95,
     "highlights": ["3x points on travel", "No foreign transaction fees"]},
    {"name": "Everyday Cash Rewards",    "category": "everyday", "annual_fee": 0,
     "highlights": ["1.5% cash back on every purchase", "No annual fee"]},
    {"name": "Fuel Saver Card",          "category": "gas_saver", "annual_fee": 0,
     "highlights": ["5% back at gas stations", "3% back on EV charging"]},
]

class CardOfferOpsTool(AgentTool):
    name = "card_offer"
    agent_name = "card_offer"       # scopes the tool to this sub-agent
    description = "Card-offer operations."
    scope = "sub_agent"

    @action("list_offers",
            description="Return a catalogue of credit-card offers.",
            params_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {"offers": {"type": "array"}}})
    async def list_offers(self, params: dict, context: dict) -> dict:
        return {"offers": _OFFERS}

register_agent_tool(CardOfferOpsTool())
```

Then register the module in `app/tools/__init__.py:init_tools()`:

```python
from app.tools import card_offer_ops  # noqa
```

Restart the backend (uvicorn reload picks up the new import). Verify with `curl http://localhost:6000/api/tools | jq '.[] | select(.name=="card_offer")'` — the tool should appear with its `list_offers` action.

**Step 2 — open the Builder UI.**

Navigate to `http://localhost:6001/agents` → click **+ Create Agent**.

**Step 3 — fill in General settings (left panel, General tab).**

- *Display Name:* `Card Offer`
- *Name (slug):* `card_offer` — this is the `agent_name` the registry uses.
- *Channel:* `chat`
- *Description:* what the Planner sees when deciding whether to call the agent. Be specific and example-driven:
  > "Recommend credit-card offers when the user asks about getting a new credit card, applying for one, or wants suggestions. Returns three options (travel, everyday, gas-saver) for the orchestrator to summarize."
- *Search Hint:* short keyword list that `tool_search` ranks against:
  > "credit card offer recommend suggest apply travel cash gas rewards new card"

**Step 4 — build the graph (centre canvas).**

The default graph is `parse → dispatch → respond`. For `card_offer` we want: `dispatch → load_offers → respond`. (`parse` isn't needed because we don't extract slots from the user message.)

- Delete the `parse` node (click → Delete in the right panel).
- Click `+` → add a **tool_call** node. Position it between `dispatch` and `respond`. Drag an edge from `dispatch` to `load_offers`, then `load_offers` to `dispatch` (so the dispatcher re-routes after the tool returns).

**Step 5 — configure the `load_offers` (tool_call_node) — right panel.**

Select the node, then in the right panel:
- *Tool:* select `card_offer` from the dropdown (the AgentTool you registered in step 1).
- *Action:* select `list_offers` (the only declared action).
- *Params:* leave as `{}`.
- *Output var:* `offers` — the runtime will write the tool's return value to `state.variables.offers`.

**Step 6 — configure the `respond` node.**

- *Return mode:* `to_orchestrator` (the main LLM will paraphrase the result; no widget needed for this demo).
- *Text template:*
  > `Here are the available card offers — please summarize them for the user and let them know they can ask follow-up questions:\n\n{{variables.offers.offers}}`

  The `{{variables.offers.offers}}` syntax pulls the offers list out of the slot (see Part 4.2 for substitution rules).

**Step 7 — wire the dispatch edges.**

Click the `dispatch` node. The right panel shows outgoing edges in order. Add three predicates (top edge wins):

1. `dispatch → load_offers` with predicate `!has(variables.offers)` — first run: fetch.
2. `dispatch → respond` with predicate `true` — after `load_offers` re-enters dispatch, this catches it.

(Predicate DSL spec: Part 4.1.)

**Step 8 — Save & Deploy.**

Top-right **Save & Deploy** button. The Builder POSTs to `/api/agents`, which:
- Validates the graph via the template loader.
- Persists the row to `sub_agent_templates`.
- Calls `refresh_dynamic_sub_agent_tools()` — your new agent is now in the tool registry as a `DynamicSubAgentTool`.

**Step 9 — test in chat.**

Open `/chat`, send "Show me your credit card offers" — the Planner should call `card_offer`, the sub-agent fetches the offers, and the chat replies with the three card descriptions.

##### Routing caveat

For a fresh sub-agent to actually get called, the Planner needs to choose `tool_search` over its default behaviour. The Planner's system prompt currently scopes itself to banking operations and may decline off-domain topics outright. If your new agent's domain isn't in the Planner's prompt sandbox, either:

- Set the agent's `always_load=true` (Settings tab → Always-load checkbox) so it's bound on every turn — verified-working short cut at the cost of a few tokens per turn.
- Or update the Planner's system prompt to include your domain. Bigger change, out of scope of agent-authoring.

#### Way B — drop a JSON file in `app/agents/templates/` (the seed path)

`initialize_templates()` at startup calls `seed_from_files()` which:

- Inserts new files as `status='deployed'`, `source='seed'`.
- **Re-syncs** existing `source='seed'` rows when the file hash changes (so PR edits to regulated templates flow into the DB on next boot).
- **Never overwrites** `source='user'` rows (business-user edits win over same-name seed files).

### Are tools and widgets selectable from the Agent Builder UI?

**Yes, both — automatically.**

- **Tools** — `AgentBuilder.jsx` calls `GET /api/tools` and populates the tool picker. Nodes that bind tools (`llm_node` with `tool_names`, `tool_call_node`) pick from this list. Any tool you register in the backend appears here on the next page load.
- **Widgets** — `WIDGET_CATALOG` in `app/widgets/catalog.py` is served via `GET /api/widgets/catalog`. The builder's `response_node` config exposes a widget option. The catalog auto-validates at module load (`_validate_catalog()`) and is fingerprinted with `CATALOG_VERSION` — frontend refetches when the hash changes.

**Adding a new tool or widget makes it selectable in the builder on the next page load without touching frontend code.**

---

## Part 3 — What happens when you "drop a JSON template"

This is the part that trips people up. Walk through it carefully.

### Boot sequence (`app/main.py:lifespan`)

```
1. init_tools()              → registers hand-coded tool classes
                                (TransferAgentTool, RefundAgentTool,
                                 GetProfileDataTool, etc.)
                                These call register_tool() at module import time.

2. initialize_templates()    → seed_from_files() reads every *.json in templates/
                                and inserts/syncs DB rows.
                                NO tool registration happens here. Just rows in
                                sub_agent_templates.

3. init_agents()             → does three sub-steps:

   3a. For each DB template row:
         register_agent_channels(name, channels)
         _AGENT_TEMPLATE[name][channel] = tpl_name

       ← Runs for EVERY template, regulated or not.
         Just records "this agent exists for these channels" in the AGENT registry.
         Does NOT make the agent LLM-callable.

   3b. register_agent_scoped_tool("transfer_money", transfer_money)
       (legacy — agent-scoped tools for free-form llm_node binding)

   3c. refresh_dynamic_sub_agent_tools()
       → For each deployed DB row that:
            - is NOT locked_for_business_user_edit
            - AND is NOT already in _REGISTRY (hand-coded tool wins)
         create DynamicSubAgentTool(name, description, search_hint, channels)
         register_tool(it)        ← THIS is what makes it LLM-callable
```

So the question "is my agent visible to the LLM?" reduces to: **after boot, is there an entry in `_REGISTRY` (the tool registry) with my agent's name?**

### The regulated-vs-non-regulated distinction

Two flags on the template (see `app/agents/template_loader.py`):

- **`is_regulated: true`** — enforces stricter validation in the loader: no `return_mode=to_presenter`, all `llm_node` must declare `output_schema`. Prevents free-form LLM output in the audit path.
- **`locked_for_business_user_edit: true`** — DB writes from the API for this template name are rejected (`PermissionError` in `upsert_template`). Edits must come through PR + code review.

`refresh_dynamic_sub_agent_tools()` **skips** rows with `locked_for_business_user_edit=true`. That's the gate between auto-registration and the hand-coded path.

### The three concrete scenarios

#### Scenario A — Non-regulated agent (the easy path)

You want a simple sub-agent. e.g. `card_lock` — a flow that asks the user which card to lock and confirms.

**Files to create:** `app/agents/templates/card_lock.chat.json`

```json
{
  "name": "card_lock_chat",
  "agent_name": "card_lock",
  "display_name": "Card Lock",
  "channel": "chat",
  "supported_channels": ["chat"],
  "description": "Lock or unlock a debit/credit card. Examples: 'lock my checking card', 'freeze the card ending in 1234'.",
  "search_hint": "lock unlock freeze card debit credit",
  "always_load": false,
  "is_regulated": false,
  "locked_for_business_user_edit": false,
  "entry_node": "parse",
  "nodes": [ ... ],
  "edges": [ ... ]
}
```

**Python you write:** none.

**What happens at boot:**

- Step 2: row inserted in `sub_agent_templates`. The seeder reads `description`, `search_hint`, and `always_load` from the JSON file and stores them on the row.
- Step 3a: agent registry says "card_lock has chat channel."
- Step 3c: `DynamicSubAgentTool(name="card_lock", description=..., search_hint=..., always_load=...)` is created, registered in `_REGISTRY`. If `always_load: true` was in the JSON, the tool is bound on every turn; otherwise it's deferred and discoverable via `tool_search`.

**Result for the LLM:**

- The Planner finds it via `tool_search("lock card")` because the `search_hint` from the JSON matches.
- When the Planner calls `card_lock(message="lock my checking card")`, `DynamicSubAgentTool.execute()` runs, looks up the template via `template_for_agent("card_lock", "chat")`, compiles the StateGraph, and drives it.

The JSON is fully self-describing — no Builder-UI follow-up needed. You can also still edit non-regulated agents in the Builder UI after seeding; those edits win over the JSON file (the seeder skips `source='user'` rows on re-runs).

#### Scenario B — Regulated agent, JSON only (the broken state)

You drop `card_lock.chat.json` with `is_regulated: true` and `locked_for_business_user_edit: true`, and you write **no Python**.

**What happens at boot:**

- Step 2: row inserted, marked regulated + locked.
- Step 3a: agent registry says "card_lock has chat channel."
- Step 3c: `refresh_dynamic_sub_agent_tools()` **skips this row** because `locked_for_business_user_edit=true`.

**Result for the LLM:** No entry in `_REGISTRY` with name `card_lock`. **The Planner cannot call it.** The agent is dead code — the template exists, the registry knows about it, but there's no tool surface.

**Don't land here. You only get into this state by forgetting Scenario C.**

#### Scenario C — Regulated agent, JSON + hand-coded tool (the Transfer/Refund pattern)

This is what `transfer_money` does. You write **two files**.

**File 1:** `app/agents/templates/card_lock.chat.json` (and `.voice.json` if applicable) with `is_regulated: true`, `locked_for_business_user_edit: true`.

**File 2:** `app/tools/card_lock_tool.py` — hand-coded `BaseTool` subclass:

```python
from app.tools import register_tool
from app.tools.base import BaseTool, ToolResult
from app.agents import template_for_agent
from app.agents.template_compiler import compile_template

class CardLockTool(BaseTool):
    name = "card_lock"
    always_load = True              # or should_defer = True
    is_read_only = False
    is_concurrency_safe = False
    widget = "card_lock_form"       # whatever your terminal widget is
    channels = ("chat", "voice")
    has_glass = True
    workflow_instructions = "..."   # multi-turn guidance for the Planner
    response_instructions = "..."   # post-tool nudges for the LLM's next turn
    flow = (...)
    errors = (...)

    async def description(self, context=None):
        return "Lock or unlock a debit/credit card. Examples: ..."

    async def input_schema(self):
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    async def execute(self, input, context):
        # Look up the template, compile, drive the inner graph, map to ToolResult.
        # Copy the structure from transfer_tool.py — same outer-interrupt pattern.
        ...

register_tool(CardLockTool())
```

**Then** add the import to `app/tools/__init__.py:init_tools()`:

```python
def init_tools():
    ...
    from app.tools import card_lock_tool  # noqa
```

**What happens at boot:**

- Step 1: `init_tools()` imports `card_lock_tool`, which calls `register_tool(CardLockTool())`. **`_REGISTRY["card_lock"]` now exists with the hand-coded tool.**
- Step 2: JSON template inserted as a DB row.
- Step 3a: agent registry says "card_lock has chat+voice channels."
- Step 3c: `refresh_dynamic_sub_agent_tools()` sees `card_lock` is locked → skips it. Even if it weren't locked, the `if agent_name in _REGISTRY and agent_name not in _DYNAMIC_REGISTERED` guard would skip it because the hand-coded tool is already there. **Hand-coded wins.**

**Result for the LLM:** The Planner sees a tool named `card_lock` with the rich description, `workflow_instructions`, etc. that you wrote. When it calls the tool, your `execute()` drives the JSON-defined StateGraph.

**Why regulated agents need this two-file pattern:** A regulated tool needs a stable, audited `description` / `workflow_instructions` / `response_instructions` that survive PR review; hand-tuned error categorization, retry policy, and response shaping; and channel-specific behavior (transfer's chat path is widget-first, voice is interrupt-heavy). `DynamicSubAgentTool` is generic — it can't carry that nuance. Regulated flows pay the cost of writing the tool class to get full control of the LLM-facing surface and full audit-trail isolation.

### Where does the LLM-facing metadata actually live?

A common question once people read the JSON templates: *"The transfer template has no `description` or `search_hint` — how does the LLM know when to call it?"*

Answer: for regulated agents, **the LLM-facing metadata lives in the Python tool class, not in the template**. The template only defines the graph. Here's the full mapping across all three authoring paths:

| Agent kind | `description` source | `search_hint` source | `always_load` source | How it reaches the LLM | DB row columns used? |
|---|---|---|---|---|---|
| **Hand-coded tool** (Transfer, Refund) | Python class `async def description()` method | Python class `search_hint` attribute | Python class `always_load` attribute | `init_tools()` registers the class; `to_openai_schema()` builds the OpenAI function spec from the class. | **No.** Template columns are blank and ignored. |
| **Non-regulated, authored in Builder UI** | `description` column on the DB row (typed in UI) | `search_hint` column on the DB row (typed in UI) | `always_load` column on the DB row (Settings tab checkbox) | `refresh_dynamic_sub_agent_tools()` reads the row, constructs a `DynamicSubAgentTool(description=..., search_hint=..., always_load=...)`, and registers it. | **Yes** — primary source. |
| **Non-regulated, seeded from JSON** | Top-level `description` key in the JSON file | Top-level `search_hint` key in the JSON file | Top-level `always_load` key in the JSON file | Same path as UI-authored — `seed_from_files()` writes the JSON's keys to the DB row at boot, then `refresh_dynamic_sub_agent_tools()` picks them up. | **Yes** — populated by the seeder from the JSON. |

### Why the transfer template has no description (and why that's correct)

For `transfer_money` specifically:

1. **The Python class wins.** `TransferAgentTool.description()` returns a 22-line, hand-tuned prompt explaining the three transfer types (m2m / zelle / cc), when to call, what NOT to ask. That's what the Planner sees. The DB row's `description` column is irrelevant.
2. **`search_hint` is also Python-side** (`search_hint = "transfer money send pay zelle credit card between accounts"`) — but it's essentially unused because `always_load=True` means the tool is bound to every turn without needing `tool_search` discovery.
3. **The DB columns stay blank by design.** The seeder doesn't write them. The Builder UI can't edit a locked row (`PermissionError`). And the `DynamicSubAgentTool` factory skips locked rows entirely (`dynamic_sub_agent_tool.py:289`). So no one reads those columns for Transfer — they're just unused.

### End-to-end: how Transfer becomes callable

1. Backend boot → `init_tools()` imports `transfer_tool.py` → `register_tool(TransferAgentTool())` puts it in `_REGISTRY` with the rich Python-side description.
2. Every chat turn → orchestrator calls `get_always_load_tools(channel)` → `TransferAgentTool` is included → its `to_openai_schema()` is built from `description()` + `input_schema()` and sent to the LLM.
3. LLM reads the 22-line description and decides whether to call `transfer_money(message=...)`.
4. On call → `TransferAgentTool.execute()` runs → looks up the JSON template via `template_for_agent("transfer_money", channel)` → compiles it to a StateGraph → drives the inner graph with the outer-interrupt pattern.

**The JSON template is the graph definition. The Python class is the LLM-facing tool surface.** For regulated agents they're two halves of the same agent — neither works without the other.

### Seed JSONs are fully self-describing

When you drop a non-regulated template into `app/agents/templates/`, the seeder reads three top-level keys from the JSON file and writes them to the DB row:

- `description` — what the LLM Planner sees
- `search_hint` — keywords for `tool_search` weighted matching
- `always_load` — bind to every turn (`true`) vs. surface via `tool_search` (`false`, the default)

So a non-regulated JSON template is a complete agent definition. No follow-up Builder-UI edit is needed for the LLM-facing surface — though you can still edit in the UI afterwards (user edits win over re-seeds, see `seed_from_files()` source='user' guard).

---

## Part 4 — The DSLs inside agent templates

When the team starts building agents, they'll hit four small DSLs that live inside the JSON template. Each one is intentionally narrow and parsed at load time — they're not Python, not Jinja, and not arbitrary expressions. Treat them as part of the template schema.

| DSL | Where it's used | Source of truth |
|---|---|---|
| **Predicate DSL** | `edges[*].predicate` (routing) | [`app/agents/predicates.py`](../app/agents/predicates.py) — module docstring is the grammar spec |
| **`{{var}}` substitution** | `text_template`, `widget_data_template`, any string field a node resolves | [`app/utils/templates.py`](../app/utils/templates.py) |
| **State-write DSL** | `parse_node.writes`, `tool_call_node.post_write`, `response_node.slot_writes` | Per-node modules in [`app/agents/nodes/`](../app/agents/nodes/) |
| **Output schema** (LLM structured output) | `parse_node` (`mode: llm`), `llm_node` | [`app/agents/nodes/parse_node.py`](../app/agents/nodes/parse_node.py), `llm_node.py` |

> **Where the team learns these:** the docstring at the top of each file IS the spec. There's no separate reference doc — the parser code and the docstring are kept in sync deliberately. For predicates specifically, the module docstring (`predicates.py:1-25`) shows the EBNF grammar — read that before writing your first predicate.

### 4.1 The Predicate DSL (edge routing)

This is the most-used DSL. Every edge in a template can carry a `predicate` string. The compiler parses it at template-load time; if it doesn't parse, the template fails validation and the agent never registers.

**Grammar summary** (full grammar in `predicates.py:1-25`):

```
expr   := or_expr
or_expr := and_expr ('||' and_expr)*
and_expr := not_expr ('&&' not_expr)*
not_expr := '!' not_expr | cmp_expr
cmp_expr := atom (('==' | '!=' | '<' | '<=' | '>' | '>=') atom)?
atom   := literal | path | '(' expr ')' | call
call   := ('has' | 'is_empty') '(' path ')'
path   := identifier ('.' identifier)*
literal := number | string | 'true' | 'false' | 'null'
```

**Path resolution** — the first segment selects a top-level state field:

| Path | Resolves to |
|---|---|
| `variables.X` | `state.variables["X"]` (supports nested dotted) |
| `last_tool_result.Y` | shorthand for `variables.last_tool_result.Y` |
| `channel`, `user_id`, `session_id`, `iteration_count`, `_terminal` | top-level state field |
| `main_context.X` | `state.main_context["X"]` |
| `<anything_else>.Y` | shorthand → `variables.<anything_else>.Y` |

**Built-in functions:**

| Call | Returns |
|---|---|
| `has(path)` | `True` if path resolves to non-None, non-empty (treats `""`, `[]`, `{}` as missing) |
| `is_empty(path)` | exact opposite of `has(path)` |

**Examples from real templates:**

```
"true"                                                          ← always-true default
"has(variables.transfer_details)"                               ← guard
"variables.transfer_type == 'zelle'"                            ← type-tag fan-out
"variables.transfer_type == 'zelle' && !has(variables.payee)"   ← combined
"is_empty(variables.transfer_details.payeeOptions)"             ← nested path empty
"has(variables.fees) && variables.fees.eligible == false"       ← nested check
```

**The None-safety rule (most important authoring rule).** The runtime is None-safe by design: missing paths resolve to `None`, equality comparisons against `None` are explicit, and arithmetic comparisons (`<`, `<=`, `>`, `>=`) against `None` return `False` rather than crashing. This means:

- **Equality (`==`, `!=`) with a missing path is well-defined.** `variables.transfer_type == 'zelle'` evaluates to `False` if `transfer_type` is missing — no guard needed. The validator will NOT warn here because the silent-False is well-defined intent (type-tag dispatch).
- **Other reads of a missing path silently False.** Bare-path bool coercions (`variables.confirmed`) and arithmetic comparisons (`variables.amount > 0`) without a `has()` guard usually indicate an authoring bug — the validator emits a warning if no sibling edge in the same dispatch group has a `has(...)` for that path.

The validator (template_loader.py) emits these as **warnings, not errors** — they show in the logs at boot under the key `[template_load_warning]`. Treat them as something to investigate before merge, not noise.

**Authoring rules — keep these in mind:**

1. **Always parse before commit.** Predicates compile at template load — a syntax error means the agent silently fails to register. If you're authoring outside the Builder UI, run `python -c "from app.agents.predicates import compile_predicate; compile_predicate('your predicate here')"` to fail fast.
2. **Use `==` / `!=` freely for type-tag fan-outs.** `variables.kind == 'X'` is the idiomatic dispatcher pattern and is None-safe.
3. **Use `has()` for "is this slot populated yet" checks.** That's what `has()` is for — distinguishing missing/empty from a populated value.
4. **Watch for the dependency-ordering warning.** If you write `variables.amount > 100` in a predicate but no sibling edge has a `has(variables.amount)`, the validator warns that the value isn't guaranteed to exist. Either add a guard (`has(variables.amount) && variables.amount > 100`) or restructure the dispatch.
5. **Edge order matters within a fan-out.** Edges are evaluated array-positional. The compiler preserves JSON order. Use this for priority routing — most specific predicate first, `"true"` last as the default-edge fallback.
6. **No side effects.** Predicates only read from state; they never mutate it. State changes happen in `parse_node` / `tool_call_node` / `response_node`.
7. **No `eval`. No dynamic code.** This DSL is intentionally locked down — it's the audit boundary for regulated flows. Don't try to extend it ad-hoc; if you need a new capability, add it to the parser and the evaluator together (see `_evaluate` in `predicates.py`).

### 4.2 The `{{var}}` substitution DSL (text and widget data)

Used in any string field a node feeds to the user — `text_template`, `widget_data_template`, etc. Resolved by `app/utils/templates.py`.

**Two modes, with one critical distinction:**

| Pattern | Behavior |
|---|---|
| `"{{some.path}}"` (entire string is one template) | **Raw passthrough** — returns the looked-up value as-is. Dict stays a dict, list stays a list, number stays a number. |
| `"Hello {{name}}, your balance is {{amount}}"` (mixed content) | **String substitution** — every `{{...}}` is replaced with `str(value)` or `""` if missing. |

This distinction matters because `widget_data_template` often needs to pass a complex nested object into a widget — using `"{{variables.transfer_details}}"` preserves the dict, while `"Details: {{variables.transfer_details}}"` would stringify it.

**Lookup order:** `state.variables` first, then top-level state. Nested dotted paths (`variables.transfer_details.payeeOptions`) are walked dict-by-dict.

**Missing variable:**
- Substitution mode → empty string `""`.
- Raw passthrough → `None`.

**Recursion:** the resolver walks dicts and lists, so `widget_data_template: {"amount": "{{variables.amount}}", "from": {"id": "{{variables.from_id}}"}}` resolves every leaf string.

**Authoring rule:** if a widget needs typed data (numbers, dicts, arrays), use the single-template form. If you accidentally embed it in a sentence, you'll get a stringified Python repr in the widget — usually obvious in QA but easy to miss.

### 4.3 The state-write DSL (`writes`, `post_write`, `slot_writes`)

Three places in a template can write into `state.variables`:

| Field | On node | Shape | When applied |
|---|---|---|---|
| `writes` | `parse_node` | `{schema_field: variable_name}` | After every successful parse. Defaults to identity (`{k: k}`) if omitted. |
| `post_write` | `tool_call_node` | flat `{variable_name: literal_value}` (JSON-serializable) | On tool success only — skipped on ERROR status. |
| `slot_writes` | `response_node` (with `return_mode: to_presenter`) | `{slot_name: "{{var}}"}` (templated) | On terminal node, written to the parent state for Presenter consumption. |

**Hard rules enforced at template load** (`template_loader.py:_validate_structure`):

- `tool_call_node.post_write` MUST be a flat dict (no nesting).
- All keys must be strings.
- All values must be JSON-serializable. The validator does a `json.dumps(value)` round-trip — if it fails, the template doesn't load.

**Why flat:** `post_write` is meant for state resets after a tool runs (e.g., clearing a slot, setting a flag). Nested structures belong inside the tool's output (which is already a `ToolResult` and gets parsed into `variables[output_var]` via the tool's `output_var` declaration). Keeping `post_write` flat makes the audit log readable.

**Authoring rule:** if you find yourself wanting to write a deep object into `post_write`, the answer is almost always to put that data into the tool's `to_llm` JSON and declare `output_var` on the tool — the runtime then writes it for you.

### 4.4 Output schemas (`parse_node` LLM mode, `llm_node`)

When a node calls the LLM with structured output, you declare the shape:

```json
"output_schema": {
  "transfer_type":  {"type": "string", "nullable": true, "enum": ["m2m", "zelle", "cc"]},
  "amount":         {"type": "number", "nullable": true},
  "from_account":   {"type": "string", "nullable": true}
}
```

**Hard rule for regulated templates** (`template_loader.py:_validate_semantics`): every `llm_node` MUST declare `output_schema`. Free-form LLM output is forbidden in the audit path. The validator REJECTS the template at load time if this is missing — the agent will not register.

**Pairing with `writes`:** the LLM returns a JSON object matching the schema; `writes` maps each field to a variable name. If `writes` is omitted, the runtime assumes identity (every schema field becomes a variable of the same name).

### 4.5 Regex parsers (`parse_node` regex mode)

Used for deterministic field extraction from user utterances. Lives in `app/agents/parsers/`. Each parser is a function decorated with `@register_parser("name")`.

**Built-in parsers** (`app/agents/parsers/__init__.py`):

| Name | Extracts |
|---|---|
| `money` | dollar amounts ("$200", "two hundred dollars") |
| `yes_no` | affirmative / negative confirmation |
| `account_keyword` | account-name keywords ("checking", "savings") |
| `last4` | a 4-digit identifier |

**Adding a new parser:** drop a function in `app/agents/parsers/` decorated with `@register_parser("name")`. Reference it in a `parse_node` extractor: `{"slot": "amount", "parser": "money"}`. No registration step beyond the decorator — the module is imported eagerly via the package's `__init__`.

**Rule:** keep parsers pure and deterministic. If you need anything fuzzy (intent classification, fuzzy matching), use `mode: llm` with an `output_schema` instead. Don't reach for an LLM inside a regex parser.

### 4.6 Where the rules come from — single source of truth

There is no separate "DSL spec doc." Each DSL is documented in the file that owns it:

| To learn… | Read… |
|---|---|
| Predicate grammar and None-safety semantics | `app/agents/predicates.py` (module docstring + `_evaluate`) |
| `{{var}}` substitution rules | `app/utils/templates.py` (module docstring) |
| `parse_node` schema (regex / llm modes, writes shape) | `app/agents/nodes/parse_node.py` (module docstring) |
| `tool_call_node` schema (post_write rules) | `app/agents/nodes/tool_call_node.py` (module docstring) |
| `response_node` (return modes, slot_writes) | `app/agents/nodes/response_node.py` (module docstring) |
| Template-level validation rules | `app/agents/template_loader.py` (`_validate_structure`, `_validate_semantics`) |

The Builder UI surfaces some of this (it injects defaults and rejects obvious malformed graphs), but the validator in `template_loader.py` is the gate. Anything that doesn't pass validation never registers as an agent — for regulated templates it's a hard error, for non-regulated it's the same hard error since both go through `load_template()`.

### 4.7 Quick rules of thumb for the team

1. **Read the source-of-truth file before authoring.** The docstring at the top of `predicates.py`, `templates.py`, or each node file IS the spec.
2. **Predicates: prefer `==`/`!=` for type tags, `has()` / `is_empty()` for slot-presence checks.** The runtime is None-safe — exploit it for type-tag dispatch, but always guard arithmetic and bare-path reads.
3. **`{{var}}` exact-match preserves type; mixed strings stringify.** Pick the one you need.
4. **Don't put nested dicts in `tool_call_node.post_write`.** Use the tool's `output_var` instead.
5. **Regulated `llm_node` requires `output_schema`.** No exceptions — the validator will refuse to load.
6. **Watch boot logs for `[template_load_warning]`.** They're real bugs roughly 80% of the time. Investigate before merge.
7. **Test predicates in a REPL** before pushing a template, or paste the JSON into the Builder UI — both will fail-fast on parse errors.

---

## Part 5 — Decision tree and reference tables

### Decision tree — which scenario am I in?

```
Are you adding a tool (single function call) or an agent (multi-step flow)?

├── Tool only
│   └── See Part 1. New BaseTool subclass + register_tool() + add import to init_tools().
│
└── Agent (multi-step flow)
    │
    ├── Is this flow regulated / audited?
    │   (handles money movement, account lock, sensitive policy decisions, etc.)
    │
    ├── No
    │   ├── Build it in the Agent Builder UI    → No restart, description/hint set in UI.
    │   └── Or drop a JSON file in templates/    → Restart needed; remember to set
    │                                              description + search_hint via UI after.
    │
    └── Yes
        │
        ├── Drop the regulated JSON template in templates/
        │   (is_regulated: true, locked_for_business_user_edit: true)
        │
        └── ALSO write a hand-coded BaseTool subclass in tools/
            and add it to init_tools().
            → Without the hand-coded tool, the agent is invisible to the Planner.
```

### What gets done where — summary table

| You want… | Files to create | Files to edit | Restart needed? | Result |
|---|---|---|---|---|
| **Tool** | `tools/<name>.py` with `BaseTool` subclass + `register_tool()` | `tools/__init__.py:init_tools()` to add the import | yes | Tool appears in `_REGISTRY`, in `/api/tools`, in Agent Builder. |
| **Non-regulated agent (JSON seed)** | `templates/<name>.<channel>.json` | none | yes | Auto-registered as `DynamicSubAgentTool`. ⚠️ description/search_hint blank — set them via Builder UI after first boot. |
| **Non-regulated agent (UI)** | none — Builder writes the DB row | none | no | Same result; description/search_hint typed in the UI. API calls `refresh_dynamic_sub_agent_tools()` on save. |
| **Regulated agent** | `templates/<name>.<channel>.json` AND `tools/<name>_tool.py` | `tools/__init__.py:init_tools()` to add the import | yes | Hand-coded tool registered, drives the JSON graph. Locked from UI edits. |
| **Regulated template, no entry tool** | `templates/...` only | nothing | — | ❌ Dead state. Agent invisible to the Planner. Don't do this. |

### Common pitfalls

| Symptom | Likely cause | Fix |
|---|---|---|
| Tool registered but Planner never calls it | Generic / weak `description()` or wrong `search_hint`. | Treat description as code; expand examples; add specific keywords to `search_hint`. |
| Tool registered but doesn't appear in `/api/tools` | Forgot to add the import to `init_tools()`. | Add `from app.tools import your_module  # noqa` to `app/tools/__init__.py:init_tools()`. |
| Tool startup error: "must set either should_defer or always_load" | Both `False` (default) or both `True`. | Set exactly one to `True`. |
| Non-regulated agent visible in Builder but Planner won't call it | Description / search_hint are the auto-generated fallback. | Edit the agent in the Builder UI to set real description + search_hint. |
| Regulated agent template exists, agent is silent | No hand-coded `BaseTool` subclass — Scenario B (the broken state). | Write the entry tool class and add it to `init_tools()`. |
| Tool calls the wrong sub-agent / picks the wrong channel | `channels` tuple on the tool excludes the active channel, or template lacks a `<channel>` variant. | Add the channel to `channels` and seed the corresponding `<name>.<channel>.json` template. |
| Sensitive identifiers leak into the orchestrator's reasoning | Used `error` field for user-facing text. | Move user-safe wording to `user_facing_message`; keep technical detail in `error` (logs only). |

---

## Part 6 — Key takeaway for the team

> **A JSON template is not enough on its own to make an agent visible to the LLM.** There has to be a tool registered with the agent's name. For non-regulated templates, `DynamicSubAgentTool` is auto-created and that's the tool. For regulated templates, you must hand-write a `BaseTool` subclass and register it. The agent registry and the tool registry are different things — the LLM only ever sees the tool registry.

If you remember nothing else, remember that.

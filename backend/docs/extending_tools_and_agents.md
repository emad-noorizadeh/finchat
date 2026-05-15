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

#### `AgentTool` scope — per-agent vs. global

Set `agent_name` on the class:

- `agent_name = "transfer"` → only the `transfer` sub-agent's `tool_call_node`s can dispatch it. Most domain tools (Transfer, Refund, CardOffer) follow this pattern.
- `agent_name = ""` → **global** AgentTool, callable from any sub-agent. The dispatcher (`get_agent_tool` at `tools/agent_tool.py:138-144`) falls back to the global bucket when no agent-scoped match exists, and `list_agent_tools_for(agent)` returns `agent-scoped + globals`. So the Builder's tool dropdown shows globals when you're editing any sub-agent.

Use the global form for shared utilities — currency formatting, generic lookups, anything multiple sub-agents would benefit from. Example skeleton:

```python
class FormatCurrencyTool(AgentTool):
    name = "format_currency"
    agent_name = ""              # global — usable by every sub-agent
    description = "Format a number as USD currency."

    @action("format", description="Format a number.",
            params_schema={"type": "object",
                           "properties": {"amount": {"type": "number"}},
                           "required": ["amount"]})
    async def format(self, params, context):
        return {"formatted": f"${params['amount']:,.2f}"}

register_agent_tool(FormatCurrencyTool())
```

No other plumbing needed — registration places it in the global bucket and the Builder picks it up automatically.

#### Which node type calls which?

The Agent Builder's tool dropdowns are filtered accordingly — getting it right at authoring time is easier than debugging "my tool doesn't get called" later.

| Sub-agent node | What it dispatches | Tool kind required |
|---|---|---|
| `tool_call_node` | `tool.dispatch(action, params, context)` — explicit `{tool, action}` from the template | **AgentTool** (with `@action(...)` declared) |
| `llm_node` + `tool_node` | LLM picks tools listed in `llm_node.tools`, then `tool_node` runs whatever `tool_calls` came out via the `tool_caller` closure | **Either kind** — works with `BaseTool` or `AgentTool` |

So:
- **Deterministic step** ("always call X to fetch Y") → `tool_call_node` + `AgentTool`.
- **LLM-decides** ("the model picks among these tools") → `llm_node` + `tool_node` with either kind in `tools`.

If you want a sub-agent to call a `BaseTool` like `knowledge_search`, use the `llm_node` + `tool_node` pattern. The `tool_call_node`'s dropdown intentionally filters BaseTools out — they don't have a `dispatch` surface and would fail at runtime.

**`tool_node` runtime detail** (DB-backed sub-agents only need to know this if they hit a bug). `tool_node` looks for a per-thread `_tool_caller` first (set by hand-coded sub-agents like Transfer). If none is registered — the path used by every `DynamicSubAgentTool` instance — it falls back to a default caller that resolves the tool from the global registry via `get_tool(name)` and dispatches: `AgentTool.dispatch(action, params, context)` if the tool exposes actions, otherwise `BaseTool.execute(args, context)`. See `app/agents/nodes/tool_node.py:_default_tool_caller`. You shouldn't need to think about this; if it breaks, that's where to look.

**`AgentTool` global scope** for cross-sub-agent reuse. An `AgentTool` subclass sets `agent_name = "foo"` to be scoped to that one sub-agent (the default — Transfer/Refund follow this). Set `agent_name = ""` to make it callable from ANY sub-agent's `tool_call_node`. The lookup chain is `(agent_name, tool_name)` first, then `("", tool_name)` as a fallback. `card_offer` is the reference for a globally-scoped AgentTool — `card_advisor`, `card_browser`, and any future card-related sub-agent share it.

**`response_node.text_source`**. When the inner LLM produces the final user-facing text (the `llm_node` + `tool_node` + summarize-`llm_node` pattern), the response_node can pull it directly from the last AIMessage instead of templating a variable. Set `text_source: "last_assistant_message"` on the response_node and omit `text_template`. The `account_qa.chat.json` template is the reference.

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
| `app/agents/templates/*.json` | Bootstrap seeds. Imported into the DB **only when the table is empty** (fresh install). After bootstrap, the DB is the sole source of truth — boots never re-sync from files. To deploy a content change for a seeded agent (regulated or otherwise), call `POST /api/agents/admin/import-file/{filename}`. See "Boot sequence" below. |
| `sub_agent_templates` table | Runtime source of truth. |
| `app/agents/patterns/*.json` | Starter skeletons (`collect_one_slot.json`, `confirm_then_execute.json`) — the Builder lets you clone these into a new graph. |

### Two ways to author an agent

#### Way A — the Agent Builder UI (the expected path for non-regulated agents)

Frontend: `frontend/src/components/agents/AgentBuilder.jsx` + `frontend/src/pages/AgentBuilderPage.jsx`. Goes through the `/api/agents` write endpoints in `app/routers/agents.py`. After a write, the router auto-calls `refresh_dynamic_sub_agent_tools()` which rebuilds the registry. **No backend restart needed.**

##### UI layout — what's where

- **Top bar** — agent name, status, **Save as Draft** / **Save & Deploy**.
- **Left panel (Settings)** — four tabs:
  - *General*: display name, slug, channel, **description** (LLM-facing), **search hint**.
  - *Prompt*: per-node prompt overview (read-only summary).
  - *Context*: per-sub-agent knowledge blob (Markdown). Auto-prepended to every LLM-calling node's system prompt unless that node opts out. See "Per-agent context (knowledge blob)" below.
  - *Settings*: response format, read-only flag, require confirmation, **Always-load** checkbox.
- **Centre — graph canvas** — drag nodes around, drag from a node handle to draw an edge. Click `+` to add a node (parse / condition / interrupt / tool_call / llm / tool / response). A small hint above the canvas reminds you of the edge convention.
- **Right panel (Node Properties)** — appears when a node is selected. Every node type has its own editor (tool dropdown for `tool_call_node`, return-mode + variant dropdowns for `response_node`, etc.). Edits persist into form state as you go; the **Save** buttons at the top push everything to `/api/agents` in one request.

##### Per-agent context (knowledge blob)

The **Context** tab (next to General / Prompt / Settings) holds a Markdown blob that travels with the sub-agent template. Use it for domain facts the agent needs to answer accurately — card comparison tables, eligibility rules, product specs, FAQ-style boilerplate — content that helps the LLM without having to be re-pasted into every node.

**How it reaches the model.** The compiler injects `template.context` into each node's data dict as `_agent_context`. At runtime, `llm_node` and `parse_node(mode=llm)` **auto-prepend** the context to the author's `system_prompt` with a blank-line separator, unless the node sets `data.include_context = false`. Per-node opt-out is exposed as the `Include agent context` checkbox in each LLM-calling editor.

**When to use it vs. an LLM-side `knowledge_search` tool.**

| Use the Context tab when… | Use a knowledge tool when… |
|---|---|
| The knowledge fits in ~1-3K tokens and changes rarely. | The corpus is too large to put in every prompt. |
| Every LLM call in this sub-agent benefits from the same context (card facts for `card_advisor`, eligibility rules for `refund_fee`). | Only specific turns need a targeted lookup, paid for one tool call. |
| You want zero retrieval latency on first turn. | You're OK with one extra hop. |

**Storage.** Single TEXT column on `sub_agent_templates`. Round-trips through the upsert API, the seed-JSON import path, and the agent detail GET endpoint. Empty string `""` is the inert default — when context is empty, the auto-prepend logic adds nothing and prompts behave exactly as before.

**Live edits.** Saving the agent re-runs `_refresh_registry()` which re-imports the template. The next turn picks up the new context automatically — no backend restart.

##### Prompt Builder: variable insertion panel

Sits directly below the `System Prompt` textarea on `llm_node` and `parse_node(mode=llm)` editors. Two sections:

- **Slots written upstream** — one button per variable that some ancestor node writes (BFS over the edge graph from the current node back to entry). Sources:
  - `parse_node` — `writes.values()` + `extractors[].slot`
  - `tool_call_node` — `output_var`
  - `interrupt_node` — `targets_slot`
  
  Each row shows the source node id so you can trace where the value comes from. Empty section is hidden — entry nodes won't have anything here.
- **State scalars** — always visible: `{{user_id}}`, `{{session_id}}`, `{{channel}}`.

**Click-to-insert.** Each button inserts `{{token}}` at the textarea's **current cursor position**, with the caret restored just after the inserted text. No need to switch to a separate "insert mode" — just keep typing.

**At runtime.** The prompt goes through `app/utils/templates.py:resolve_templates(state)` immediately before being sent to the LLM, so `{{variables.X}}` reflects the slot value at *this* node's execution time, not at compile time. Missing references resolve to empty string — same convention used by `interrupt_node.prompt_template` and `response_node.text_template`.

**Authoring rule.** Reference upstream slots by their exact slot name (the `{slot}` you defined in the upstream `parse_node` extractor / `tool_call_node.output_var`). The panel only lists names that some upstream node writes, so if a slot you expect isn't there, the upstream wiring is broken — fix the graph before fixing the prompt.

##### Edge conventions

- **Handle colour**: 🟢 green dots are **sources** (drag FROM), 🔵 blue dots are **targets** (drop ON). Each node has six handles total — top (target), bottom (source), and left/right pairs split on Y so forward and loop edges between the same pair don't overlap visually. Handles grow + show a ring on hover so you can see exactly where to grab.
- **Direction**: every edge has an **arrowhead** at the target end. Drag from a green source dot to a blue target dot.
- **Panel alternative (Condition node)**: when a condition_node is selected, the right panel exposes a `+ Add outgoing edge` button under the existing edges list. Click it to pick a target from a dropdown — useful for fan-out patterns where dragging multiple edges out of the same node is fiddly.
- **Solid grey** = authored edge in your template.
- **Dashed blue** = loop edge (e.g. `tool_call → dispatch` re-entry).
- **Dashed orange ("runtime")** = synthetic edge the compiler injects at runtime — e.g. from a condition_node to a `response_node` with the **failure** or **escape** variant. You don't author these; they appear in the canvas so the routing is visible.
- **Edge labels**: by default, edges show no label. **Condition node fan-outs** show a small `#N` priority badge (execution order matters). Authors can set a custom label per edge in the edge editor (right panel when an edge is selected).

##### Editing an edge

- **Click an edge** → the right panel switches to the edge editor: from/to (read-only), predicate (DSL textarea), label, edge order, ↑/↓ reorder buttons, **Delete edge**.
- **Drag an edge's endpoint** to a different node's handle to **re-route**.
- **Press `Delete`** with an edge selected to remove it.
- Runtime-injected (dashed orange) edges are not selectable — they're a visual hint only.

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
- *Variant:* `Normal` (default). Use `Failure` for a node that handles tool errors and `Retry exhausted` for slot-capture giveups — those variants light up coloured badges on the node and the compiler auto-routes the `condition_node` to them. (Older templates picked these up via id/label regex; new templates should set the explicit Variant.)
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

`initialize_templates()` at startup calls `bootstrap_from_files()` which:

- **Runs once, only when the `sub_agent_templates` table is empty** (fresh install / wiped DB). Inserts every *.json file as `status='deployed'`, `source='seed'`.
- **Does nothing on subsequent boots**, even if the JSON content changed. The DB is the sole source of truth after bootstrap. No auto-resync — that historical behaviour was removed because it could silently clobber UI edits.
- To deploy a content change for a seeded agent (regulated or otherwise) after bootstrap, call `POST /api/agents/admin/import-file/{filename}`. That re-applies a single JSON file deliberately and audit-logs the action.

Source-column semantics after this change:

- `source='seed'` — the row was last populated by bootstrap **or** by an explicit admin import. The label tracks "where the canonical content came from".
- `source='user'` — the row was last written through the Builder UI / `/api/agents` write endpoints. Any UI edit flips a previously-seeded row to `'user'` to keep the label honest.
- Source is **informational** — it does not affect boot behaviour. The DB always wins on subsequent boots.

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

2. initialize_templates()    → bootstrap_from_files() runs ONLY if the
                                sub_agent_templates table is empty. On a
                                fresh install it inserts every *.json in
                                templates/ as source='seed', status='deployed'.
                                On any subsequent boot it's a no-op.
                                Updating a seeded template after bootstrap
                                requires POST /api/agents/admin/import-file/<f>.
                                NO tool registration happens here. Just rows
                                in sub_agent_templates.

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

- Step 2: row inserted in `sub_agent_templates` — but only if the table was empty when the process started (bootstrap). If the DB already has rows from a previous boot, this file is **NOT** auto-loaded; you'd hit `POST /api/agents/admin/import-file/card_lock.chat.json` to apply it. The reader pulls `description`, `search_hint`, and `always_load` from the JSON file in either path.
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
| **Non-regulated, seeded from JSON** | Top-level `description` key in the JSON file | Top-level `search_hint` key in the JSON file | Top-level `always_load` key in the JSON file | Same path as UI-authored — `bootstrap_from_files()` (fresh install) or `import_template_file()` (admin import) writes the JSON's keys to the DB row, then `refresh_dynamic_sub_agent_tools()` picks them up. | **Yes** — populated from the JSON at bootstrap or admin import. |

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

When you drop a non-regulated template into `app/agents/templates/`, three top-level keys are read from the JSON file and written to the DB row at bootstrap (or admin import):

- `description` — what the LLM Planner sees
- `search_hint` — keywords for `tool_search` weighted matching
- `always_load` — bind to every turn (`true`) vs. surface via `tool_search` (`false`, the default)

So a non-regulated JSON template is a complete agent definition. No follow-up Builder-UI edit is needed for the LLM-facing surface — though you can still edit in the UI afterwards. A UI edit flips the row's `source` from `'seed'` to `'user'`, and that's where it stays; the JSON file no longer affects the row until you deliberately call `POST /api/agents/admin/import-file/<filename>` (which forces source back to `'seed'`).

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

### 4.2 The `{{var}}` substitution DSL (text, widget data, and LLM prompts)

Used in any string field a node feeds either to the user or to an LLM — `text_template`, `widget_data_template`, `interrupt_node.prompt_template`, `tool_call_node.params_template`, and **`llm_node.system_prompt` / `parse_node.system_prompt` (mode=llm)**. Resolved by `app/utils/templates.py`.

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

### 4.6 The `interrupt_node` lifecycle — pause, resume, and what the LLM does NOT see

`interrupt_node` is the only node type that suspends the conversation and waits for the user. It is the right tool when a sub-agent needs to ask a clarifying question mid-flow (e.g. "What matters most — travel, cashback, or gas?"). It is the WRONG tool for asking confirmation on a side-effecting action — use a `confirmation_request` widget for that.

The mechanics are deliberately unlike LangGraph's built-in tool-level interrupt, so read this section before authoring a flow that uses one.

#### What the node itself does

`app/agents/nodes/interrupt_node.py`:

1. Resolves `prompt_template` against state (`{{var}}` substitution).
2. Picks the channel-appropriate template: `voice_prompt_template` if `channel == "voice"`, otherwise `prompt_template`. The Agent Builder hides the voice field when the template's `supported_channels` is chat-only.
3. Writes `variables._pending_interrupt_payload = {kind: "slot_prompt", prompt, channel, targets_slot}` and sets `last_prompted_slot`.
4. Returns. The compiler short-circuits every interrupt_node's outgoing edge to `END`, so **the inner graph terminates here**. The node itself does NOT call LangGraph's `interrupt()`.

#### Why the node doesn't call `interrupt()` directly

The inner graph has no checkpointer (only the outer orchestrator does). Calling LangGraph's `interrupt()` inside an unchecked-pointed graph raises with no replay context. So the design lifts the interrupt one level up: the inner graph parks a payload on state, terminates, and the outer driver does the actual pause.

#### The outer driver loop

`app/tools/dynamic_sub_agent_tool.py:153-168`:

```python
while _has_pending_interrupt(inner_state):
    payload, inner_state = _consume_pending(inner_state)
    save_inner_state(thread_id, inner_state)

    resume_value = interrupt(payload)              # ← THIS is the real pause
    user_text = _coerce_user_text(resume_value)    # extracts data.utterance

    inner_state = load_inner_state(thread_id) or inner_state
    inner_state["messages"].append(HumanMessage(content=user_text))
    escape_update = apply_resume_escape(inner_state, user_text)
    ...
    inner_state = await _run_inner_once(graph, inner_state, inner_config)
```

`interrupt(payload)` lives inside `DynamicSubAgentTool.execute()`, which is called from the orchestrator's `tool_execute` node. So the pause happens **inside the sub-agent's tool call from the orchestrator's perspective** — the outer checkpoint captures the suspended generator and can replay later.

#### On resume — the Planner is NOT consulted

This is the part most people get wrong. When the chat router receives `req.type == "resume"`:

```python
# routers/chat.py:240-241
async for event in compiled.astream_events(
    Command(resume=req.data), config=config, version="v2"
):
```

`Command(resume=...)` returns directly into the paused `interrupt(payload)` call inside the sub-agent's driver loop. The orchestrator graph **does not re-enter `planner_llm`**. There is no LLM call between the user's reply landing on the server and the inner graph re-running.

What that means in practice:

* The Planner never sees the user's reply. No contextualization, no rewording, no second look at the chat history.
* The raw utterance is appended to the inner sub-agent's `messages` as a `HumanMessage` verbatim.
* The sub-agent's `parse_node` is what reads it. Its system prompt must be narrow enough that the raw reply (often a fragment like "I drive a lot for work") is enough to extract the slot.

If you need richer context inside parse_node, surface it as a `{{variable}}` in the system_prompt (template substitution is supported) — don't expect the Planner to brief the sub-agent on resume, because it won't be running.

#### Sub-agent state during the pause

Everything the inner graph accumulated is preserved across the interrupt:

* `inner_state["variables"]` — slot values parsed in earlier runs survive.
* `inner_state["messages"]` — full inner-graph conversation (the initial HumanMessage from the Planner's `message` arg + any AIMessages from earlier nodes + new HumanMessages on each resume).
* `inner_state["last_prompted_slot"]` — which slot the user is replying to.

The outer driver re-invokes the inner graph **from `entry_node`** on each resume, not from the interrupt_node. The inner graph's pure nodes are expected to be idempotent: parse_node re-runs, but `{{variables.X}}` reads carry over, so previously-parsed values aren't re-asked. The dispatcher (condition_node) reads the augmented state and routes to a different branch (e.g. from `ask` to `respond_gas`).

The orchestrator's outer session history (other turns, other sub-agent calls, profile data) is **NOT** in `inner_state`. Sub-agents are sandboxed to the slice of conversation the Planner handed them via the initial `message` arg, plus subsequent resume utterances.

#### Channel handling and persistence

* The frontend renders chat `slot_prompt` events as a regular assistant text message. The Voice channel substitutes a TTS-friendly `voice_prompt_template`. The Agent Builder hides the voice field when the template is chat-only.
* The chat router persists the prompt as an assistant message with `message_type="slot_prompt"` before yielding the SSE `interrupt_event`. On page reload, the prompt is restored from DB and the frontend re-arms a `pendingResume` flag by scanning the last assistant message. The next user message then POSTs with `type: "resume"`.
* Sub-agents that emit a `confirmation_request` widget (e.g. transfer) go through a different path — the widget itself is persisted, and resume is driven by the user clicking Confirm/Cancel. Don't double-handle that case with an interrupt_node.

#### Edge cases and gotchas

* **Ambiguous reply → silent re-prompt.** If parse_node returns `null` for the slot (the LLM couldn't map the reply), condition_node will typically dispatch back to the same `ask` node and the interrupt fires again. This is by design — natural retry — but it can feel like a loop if your parse_node prompt is too strict. Loosen the slot-extraction examples first; don't add error branches.
* **Escape classifier (`apply_resume_escape`).** Before re-running the inner graph, the driver classifies the resume utterance for abort / topic-change intent. If it's "cancel" or a topic switch, `_escape_kind` is set on state — your sub-agent can route to a graceful exit branch by checking `variables._escape_kind == 'abort'`. Default behaviour returns the user to the orchestrator with a short acknowledgment.
* **Multiple sequential interrupts.** The driver loop is a `while _has_pending_interrupt(...)` — a sub-agent can prompt for several slots one at a time, each pause yielding back to the outer checkpoint. Each resume re-runs the inner graph from `entry_node`. Cost: parse_node runs N times for N slots; design accordingly.
* **No `interrupt_node` for tool dispatch.** If you want to pause for a confirmation BEFORE a tool fires, prefer the tool's own interrupt + widget pattern (Transfer/Refund) rather than wedging an interrupt_node in front of it.
* **Don't expect the Planner to remember context the sub-agent collected.** When the sub-agent returns to the orchestrator, the only thing flowing back is the `ToolResult` (text, widget, or slot_writes). If you want long-lived state, write it to the orchestrator's `variables` via `return_mode: "to_presenter"` with `slot_writes`.

#### What to do when authoring an interrupt_node

1. Write a tight `prompt_template` with one clear question. Add a `voice_prompt_template` only if the template supports voice.
2. Set `targets_slot` to the variable name parse_node will write on the next resume run.
3. Make sure parse_node's system_prompt has examples that cover the kinds of replies users will give to YOUR prompt — fragments, full sentences, edge phrasings.
4. Ensure the condition_node downstream of parse_node has a `predicate: "true"` fallback edge back to your `ask` node, so a `null` extraction loops cleanly.
5. Test in the Builder: `card_advisor.chat.json` is the reference flow (parse → dispatch → ask → resume → respond_*).

### 4.7 Where the rules come from — single source of truth

There is no separate "DSL spec doc." Each DSL is documented in the file that owns it:

| To learn… | Read… |
|---|---|
| Predicate grammar and None-safety semantics | `app/agents/predicates.py` (module docstring + `_evaluate`) |
| `{{var}}` substitution rules | `app/utils/templates.py` (module docstring) |
| `parse_node` schema (regex / llm modes, writes shape) | `app/agents/nodes/parse_node.py` (module docstring) |
| `tool_call_node` schema (post_write rules) | `app/agents/nodes/tool_call_node.py` (module docstring) |
| `response_node` (return modes, slot_writes) | `app/agents/nodes/response_node.py` (module docstring) |
| `interrupt_node` mechanics and resume contract | `app/agents/nodes/interrupt_node.py` + `app/tools/dynamic_sub_agent_tool.py:153-168` |
| Template-level validation rules | `app/agents/template_loader.py` (`_validate_structure`, `_validate_semantics`) |

The Builder UI surfaces some of this (it injects defaults and rejects obvious malformed graphs), but the validator in `template_loader.py` is the gate. Anything that doesn't pass validation never registers as an agent — for regulated templates it's a hard error, for non-regulated it's the same hard error since both go through `load_template()`.

### 4.8 Quick rules of thumb for the team

1. **Read the source-of-truth file before authoring.** The docstring at the top of `predicates.py`, `templates.py`, or each node file IS the spec.
2. **Predicates: prefer `==`/`!=` for type tags, `has()` / `is_empty()` for slot-presence checks.** The runtime is None-safe — exploit it for type-tag dispatch, but always guard arithmetic and bare-path reads.
3. **`{{var}}` exact-match preserves type; mixed strings stringify.** Pick the one you need.
4. **Don't put nested dicts in `tool_call_node.post_write`.** Use the tool's `output_var` instead.
5. **Regulated `llm_node` requires `output_schema`.** No exceptions — the validator will refuse to load.
6. **Watch boot logs for `[template_load_warning]`.** They're real bugs roughly 80% of the time. Investigate before merge.
7. **Test predicates in a REPL** before pushing a template, or paste the JSON into the Builder UI — both will fail-fast on parse errors.
8. **`interrupt_node` does not re-run the Planner on resume.** The user's reply lands raw in the sub-agent's `parse_node`. Make the parse_node prompt tolerant of fragments and provide examples that match how users actually reply to YOUR question. See 4.6.

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

# Building a Sub-Agent — Tutorial Deck

A slide-by-slide outline you can lift into your deck. Each slide has a heading, talking points (bullets), and a "speaker notes" block where helpful. Code blocks are sized to fit a slide; longer JSON has been trimmed with `…` to keep it readable.

---

## Slide 1 — Title

**Building a Sub-Agent**
*Template-backed, LangGraph-compiled, deterministic by design*

Speaker notes:
- 30-minute walkthrough.
- By the end, you'll have built a working sub-agent end-to-end.
- Reference doc: `backend/docs/sub_agents.md` + `backend/docs/extending_tools_and_agents.md`.

---

## Slide 2 — What is a sub-agent?

A **JSON template** that compiles into a **LangGraph `StateGraph`** at first use.

The main orchestrator's Planner calls the sub-agent as a tool; the sub-agent then drives its own internal graph **deterministically** — predicates pick routes, not LLMs.

Speaker notes:
- Sub-agents handle multi-step flows (e.g. transfer money, refund a fee, recommend a card).
- The Planner only calls tools — to make a sub-agent reachable, *something* has to register a tool whose `execute()` runs the template's graph.

---

## Slide 3 — Why this shape

The previous LLM-driven approach asked the model what to do at every step:
- Malformed tool payloads.
- Skipped confirmations.
- Dead loops.

The template-backed design splits responsibility four ways:

| Layer | Role |
|---|---|
| Tool | Business rules, auth, limits, fraud. |
| Template | Procedure shape: nodes + predicate-routed edges. |
| Sub-agent runtime | Drives the compiled `StateGraph` deterministically. |
| Orchestrator | Routes intent and paraphrases the return value. |

---

## Slide 4 — Graph-level view

```
User
  │ POST /api/chat/sessions/:id/messages
  ▼
Main orchestrator (Planner + Presenter)
  │ calls e.g. transfer_money or card_advisor tool
  ▼
{Sub-agent}Tool.execute  (e.g. DynamicSubAgentTool)
  │ loads the (agent_name, channel) → template
  │ compiles it once (cached)
  ▼
Inner StateGraph
  │ runs entry → … → response_node
  │ pauses at interrupt_node for user input
  ▼
ToolResult back to the Planner — widget, glass, or text
```

Speaker notes:
- Each `interrupt_node` ends the inner graph cleanly.
- The outer driver saves the inner state, re-presents the prompt, then re-enters from `entry_node` with the user's reply appended.

---

## Slide 5 — Node grammar (v4)

Eight node types, registered in `app/agents/nodes/`:

| Type | Purpose |
|---|---|
| `parse_node` | Extract values from the user utterance into `state.variables`. Modes: `regex` or `llm`. Skips/narrows itself when Planner-filled parameters already seeded its slots (see Slide 6b). |
| `condition_node` | Pure-Python routing. Predicate-DSL on each edge. First-match-wins. **No LLM.** |
| `interrupt_node` | Pause execution, surface a prompt, end the inner graph. |
| `tool_call_node` | Deterministically dispatch `AgentTool.dispatch(action, params, ctx)`. |
| `parallel_tools_node` | Fan out several `AgentTool` dispatches concurrently inside one node; merged `variables` write. |
| `llm_node` | Free-form LLM call with bound tool subset. Non-regulated only (regulated requires `output_schema`). |
| `tool_node` | Executes `tool_calls` emitted by the preceding `llm_node` (in parallel). |
| `response_node` | Terminal. Modes: `widget` / `glass` / `to_presenter` / `to_orchestrator`. |

A typical flow: `parse → condition → (tool_call / interrupt)+ → response`.

---

## Slide 6 — Template anatomy

`app/agents/templates/*.json` (seed) and the `sub_agent_templates` DB table (runtime source of truth).

```json
{
  "name": "card_advisor_chat",
  "agent_name": "card_advisor",
  "channel": "chat",
  "template_schema_version": 1,
  "is_regulated": false,
  "supported_channels": ["chat"],
  "suspend_resume_allowed": false,
  "locked_for_business_user_edit": false,
  "description": "…",         // what the Planner sees
  "search_hint": "…",          // keywords for tool_search
  "always_load": false,
  "context": "# Card comparison\n…",   // per-agent knowledge blob
  "parameters": { … },                  // Planner-fillable parameters (Slide 6b)
  "entry_node": "parse_open",
  "nodes": [ … ],
  "edges": [ … ]
}
```

---

## Slide 6b — Planner-fillable parameters

The orchestrator LLM already read the user's message when it picked this
agent — `parameters` lets it hand over what it understood, instead of the
sub-agent re-extracting it with a second LLM call.

```json
"parameters": {
  "properties": {
    "amount": {"type": "number", "description": "USD amount, ONLY if stated. Omit otherwise."},
    "transfer_type": {"type": "string", "enum": ["m2m", "cc", "zelle"], "description": "…"}
  },
  "required": [],                      // keep empty — required invites Planner guesses
  "writes": {"amount": "amount"}     // param → variable, defaults to identity
}
```

Mechanics:
- Declared properties merge into the entry tool's OpenAI schema next to
  `message`, so the Planner fills them **in the same call that invokes the
  agent** — zero extra cost.
- Valid values seed `state.variables` before the graph runs. Invalid or
  empty values are **dropped, never errors** (`[sub_agent_arg_dropped.v1]`)
  — the flow degrades to the normal parse/interrupt path.
- The entry `parse_node` then skips its LLM call entirely when every slot
  it writes is filled (`[parse_node_skipped.v1]`), or narrows its schema to
  the missing fields (`[parse_node_narrowed.v1]`). This applies **only on
  the Planner-entry pass** — interrupt replies always get a full parse, so
  corrections ("no, make it $30") keep working. Opt out per node with
  `always_run: true`.
- Seeding is applied **once per Planner tool_call** (keyed by tool_call id):
  interrupt replays never re-apply stale args, and a new Planner call over
  an abandoned flow fills empty slots only.
- Safety: a parameter may never write to an interrupt's `targets_slot` or a
  node `output_var` — checked across ALL channel variants at save time, so
  a confirmation gate (e.g. voice `confirmed`) stays structurally
  unreachable to the Planner. Data-collection interrupts opt in per node
  with `"planner_fillable": true` (pre-filled slot ⇒ interrupt skipped).
- Parameters are agent-level: stored per-row, auto-synced across chat/voice
  variants (same pattern as `knowledge_collections`).

Authoring: Agent Builder → left panel → **Parameters** tab (row editor +
raw-JSON mode). Parameter descriptions are the Planner's only guidance —
always say when to fill the value and to omit anything the user didn't state.

---

## Slide 7 — Mental model: the two registries

This is the single most important concept.

```
Tool registry    app/tools/__init__.py:_REGISTRY
  ↓ what the LLM Planner sees
  ↓ to be LLM-callable, a BaseTool must live here

Agent registry   app/agents/__init__.py:_AGENT_NAMES / _AGENT_TEMPLATE
  ↓ metadata about templates that exist
  ↓ says "agent X has chat+voice variants"
  ↓ does NOT make X callable on its own
```

**A template alone is not callable.** It's a graph definition. Something must register a `BaseTool` whose `execute()` runs that template's graph.

That "something" is either:
- `DynamicSubAgentTool` — generic wrapper, auto-created for non-regulated templates.
- A hand-coded `BaseTool` subclass — like `TransferAgentTool`.

---

## Slide 8 — Two ways to author

| Path | When |
|---|---|
| **Agent Builder UI** (`/agents/builder`) | Non-regulated agents — fast iteration, no restart. |
| **Seed JSON** in `app/agents/templates/*.json` | Bundled-with-image; loaded once when the DB table is empty. |

Both paths produce the same shape of row in `sub_agent_templates`. The DB is the source of truth after bootstrap.

Updating a seeded template after first boot? Use `POST /api/agents/admin/import-file/{filename}` — bootstrap doesn't re-sync.

---

## Slide 9 — Regulated vs non-regulated

Two flags on the template:

- `is_regulated: true` — stricter loader validation:
  - No `return_mode=to_presenter`.
  - Every `llm_node` MUST declare `output_schema`.
- `locked_for_business_user_edit: true` — DB writes from the API for this template are rejected. Edits go through PR + review.

For **regulated** agents, you also write a **hand-coded `BaseTool`** subclass (the Transfer/Refund pattern). Without it, the agent is invisible to the Planner.

For **non-regulated**, `DynamicSubAgentTool` handles registration automatically.

---

## Slide 10 — BaseTool vs AgentTool

The #1 reason a new tool "doesn't get called" is picking the wrong kind.

| | `BaseTool` | `AgentTool` |
|---|---|---|
| Caller | Orchestrator's Planner LLM | A sub-agent's `tool_call_node` |
| Discovery | `tool_search` or always-load | Template names it: `{tool, action}` |
| Method | `async def execute(input, context)` | `@action("name", …) async def handler(...)` |
| Registration | `register_tool(YourTool())` | `register_agent_tool(YourTool())` |

**Rule of thumb:** If the Planner should call it directly → `BaseTool`. If it's a step inside a sub-agent flow → `AgentTool`.

---

## Slide 11 — The Predicate DSL

Every condition_node edge can carry a `predicate` string. Compiled at template-load time — syntax errors mean the agent silently fails to register.

```
"true"                                                  ← default
"has(variables.transfer_details)"                       ← guard
"variables.transfer_type == 'zelle'"                    ← type tag
"variables.transfer_type == 'zelle' && !has(payee)"     ← combined
"is_empty(variables.options)"                           ← empty check
```

Spec: `app/agents/predicates.py` (module docstring is the grammar).

**None-safety:** missing paths resolve to `None`. `==`/`!=` are well-defined; arithmetic comparisons against missing values return `False`.

---

## Slide 12 — `{{var}}` substitution

Used in `text_template`, `widget_data_template`, `interrupt_node.prompt_template`, `tool_call_node.params_template`, `llm_node.system_prompt`, `parse_node(mode=llm).system_prompt`.

```
"{{some.path}}"                         ← raw passthrough; preserves type
"Hello {{name}}, balance: {{amount}}"   ← string substitution
```

**Lookup order:** `state.variables` first, then top-level state.

**Missing reference:**
- Substitution mode → `""`.
- Passthrough mode → `None`.

---

## Slide 13 — State-write DSL

Three places that can write into `state.variables`:

| Field | On node | Shape | Applied |
|---|---|---|---|
| `writes` | `parse_node` | `{schema_field: variable_name}` | After every parse |
| `post_write` | `tool_call_node` | **flat** `{var: literal}` | On tool success only |
| `slot_writes` | `response_node` (`to_presenter`) | `{slot: "{{var}}"}` | Terminal — to parent state |

**Hard rule:** `post_write` MUST be a flat dict, all JSON-serializable. Nested data belongs in the tool's `output_var`.

---

## Slide 14 — Walkthrough: build `card_offer` (1/5)

Goal: when a user says *"show me your card offers"*, return three card suggestions for the orchestrator to paraphrase.

Graph shape: `dispatch → load_offers → respond`.

**Step 1 — Write the AgentTool.** `app/tools/card_offer_ops.py`:

```python
from app.tools.agent_tool import AgentTool, action, register_agent_tool

_OFFERS = [
    {"name": "Globetrotter Travel Card", "category": "travel", …},
    {"name": "Everyday Cash Rewards",    "category": "everyday", …},
    {"name": "Fuel Saver Card",          "category": "gas_saver", …},
]

class CardOfferOpsTool(AgentTool):
    name = "card_offer"
    agent_name = "card_offer"
    description = "Card-offer operations."

    @action("list_offers",
            description="Return a catalogue of credit-card offers.",
            params_schema={"type": "object", "properties": {}})
    async def list_offers(self, params, context):
        return {"offers": _OFFERS}

register_agent_tool(CardOfferOpsTool())
```

---

## Slide 15 — Walkthrough: build `card_offer` (2/5)

**Step 2 — Wire the module import** in `app/tools/__init__.py`:

```python
def init_tools():
    …
    from app.tools import card_offer_ops  # noqa
```

Restart the backend. Verify with:

```bash
curl http://localhost:6000/api/tools | jq '.[] | select(.name=="card_offer")'
```

The tool should appear with its `list_offers` action.

**Step 3 — Open the Agent Builder** at `http://localhost:6001/agents` → **+ Create Agent**.

---

## Slide 16 — Walkthrough: build `card_offer` (3/5)

**Step 4 — General settings** (left panel → General tab):

| Field | Value |
|---|---|
| Display name | Card Offer |
| Slug | `card_offer` |
| Channel | chat |
| Description | "Recommend credit-card offers when the user asks about getting a new credit card, applying for one, or wants suggestions. Returns three options for the orchestrator to summarize." |
| Search hint | `credit card offer recommend suggest apply travel cash gas rewards new card` |

Speaker notes:
- **Description is the contract with the LLM.** Treat it like code — review it like code.
- **Search hint is the contract with the Planner.** Keywords, not prose.

---

## Slide 17 — Walkthrough: build `card_offer` (4/5)

**Step 5 — Build the graph** (centre canvas):

1. Delete the default `parse` node — we don't extract slots.
2. Add a `tool_call_node` between `dispatch` and `respond`.

**Step 6 — Configure `load_offers` (right panel):**

- Tool: `card_offer`
- Action: `list_offers`
- Params: `{}`
- Output var: `offers`

**Step 7 — Configure `respond` (right panel):**

- Return mode: `to_orchestrator`
- Text template:
  > `Here are the available card offers — please summarize them for the user:\n\n{{variables.offers.offers}}`

---

## Slide 18 — Walkthrough: build `card_offer` (5/5)

**Step 8 — Wire dispatch edges** (top-to-bottom; first match wins):

```
dispatch → load_offers      predicate: !has(variables.offers)
dispatch → respond          predicate: true

load_offers → dispatch      (re-enter the dispatcher after the tool returns)
```

**Step 9 — Save & Deploy.** The Builder POSTs to `/api/agents`, validates the graph, persists the row, and calls `refresh_dynamic_sub_agent_tools()`. **No backend restart needed.**

**Step 10 — Test in chat:** *"Show me your credit card offers"*. The Planner calls `card_offer`, the sub-agent fetches, and the chat replies with three card descriptions.

---

## Slide 19 — Per-agent context (knowledge blob)

The **Context** tab holds a Markdown blob that travels with the template. The compiler injects it as `_agent_context` into every node's data dict; `llm_node` and `parse_node(mode=llm)` auto-prepend it to their system prompt (unless `include_context: false`).

Use it for facts every LLM call benefits from:
- Card comparison tables.
- Eligibility rules.
- Product specs.
- FAQ boilerplate.

When NOT to use it:
- Corpus too large to fit in every prompt → use `knowledge_search` instead.
- Per-turn lookups → one extra hop is cheaper than bloating every prompt.

---

## Slide 20 — `interrupt_node` lifecycle

`interrupt_node` is the only node that suspends the conversation and waits for the user.

Key mechanics:

1. The node parks a `_pending_interrupt_payload` on state and returns — the inner graph terminates at `END`.
2. The outer driver loop calls LangGraph's `interrupt(payload)`, which checkpoints inside the orchestrator.
3. On `Command(resume=…)`, the user's reply lands raw in the inner graph's `parse_node`. **The Planner does NOT run on resume.**
4. The inner graph re-runs from `entry_node` with accumulated state preserved.

**Authoring rule:** parse_node prompts must tolerate fragments. The Planner won't brief the sub-agent on resume.

---

## Slide 21 — Escape classifier (runtime guarantee)

`app/agents/escape.py`. Every user reply that re-enters the inner graph is classified BEFORE the parse_node sees it:

- `abort` — user wants out ("cancel", "nevermind") → route to escape-target `response_node`.
- `topic_change` — user changed the subject → same route.
- `continue` — proceed with the parse as normal.

**Authors cannot disable this.** The compiler injects a priority-0 edge on every condition_node checking `has(variables._escape_kind)`. These show up as dashed orange in the canvas.

---

## Slide 22 — Locked principles

1. **Tools own business rules; sub-agents own experience.**
2. **Routing is deterministic** — predicates, not LLMs, decide the next node.
3. **Step-up auth is never a sub-agent concern** — the tool layer handles it.
4. **Escape classifier is a runtime guarantee** — not a node, not skippable.
5. **Partial-slot persistence is OFF by default**; opt in via `suspend_resume_allowed`.
6. **Regulated templates** can't use `to_presenter` or free-form `llm_node`.
7. **`tool_call_node.post_write` must be a flat dict.**
8. **Slot names may appear in `POLICY_BLOCK` reasons; slot values may not.**
9. **Log schema is versioned** (`.v1`, `.v2` etc).
10. **Sub-agents cannot invoke other sub-agents.** Cross-flow goes via the orchestrator.

---

## Slide 23 — Boot sequence

```
1. init_tools()
   → registers hand-coded tool classes (TransferAgentTool, …).

2. initialize_templates()
   → bootstrap_from_files() runs ONLY if sub_agent_templates is empty.
   → On any subsequent boot it's a no-op.

3. init_agents() — three sub-steps:
   3a. For each DB template row: register_agent_channels(name, channels)
   3b. register_agent_scoped_tool (legacy)
   3c. refresh_dynamic_sub_agent_tools()
       → For each deployed, NOT-locked DB row NOT already in _REGISTRY:
         create DynamicSubAgentTool, register_tool(it).
         ← THIS is what makes it LLM-callable.
```

So: "Is my agent visible to the LLM?" reduces to "Is there an entry in `_REGISTRY` with my agent's name after boot?"

---

## Slide 24 — File map (where to look)

| Concern | File |
|---|---|
| Template → `StateGraph` compiler | `app/agents/template_compiler.py` |
| Template validator + `LoadedTemplate` | `app/agents/template_loader.py` |
| DB-backed template store | `app/agents/template_store.py` |
| Node factories | `app/agents/nodes/` |
| Predicate DSL compiler | `app/agents/predicates.py` |
| `{{var}}` substitution | `app/utils/templates.py` |
| Per-thread driver runtime | `app/agents/runtime.py` |
| Escape classifier | `app/agents/escape.py` |
| Regex parsers + LLM structured-output helper | `app/agents/parsers/` |
| Seed JSON templates | `app/agents/templates/*.json` |
| Builder frontend | `frontend/src/components/agents/AgentBuilder.jsx` |
| Canvas + property panel | `frontend/src/components/agents/graph/` |

---

## Slide 25 — Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Tool registered, Planner never calls it | Weak `description` / wrong `search_hint`. | Treat description as code; expand examples. |
| Tool registered, missing from `/api/tools` | Forgot to add import to `init_tools()`. | Add `from app.tools import … # noqa`. |
| Boot error: "must set either should_defer or always_load" | Both `False` or both `True`. | Set exactly one. |
| Non-regulated agent visible but never called | Description/hint are auto-fallback. | Edit in Builder UI. |
| Regulated agent template exists, agent silent | No hand-coded `BaseTool` subclass. | Write the entry tool, add to `init_tools()`. |
| Wrong sub-agent / channel picked | `channels` excludes active channel, or template missing for `<channel>`. | Add channel + seed the `<name>.<channel>.json` template. |

---

## Slide 26 — Decision tree

```
Are you adding a tool or an agent?

├── Tool only
│   └── New BaseTool subclass + register_tool() + add import.
│
└── Agent (multi-step flow)
    │
    └── Is it regulated / audited?
        │
        ├── No
        │   ├── Build in the Agent Builder UI    → no restart.
        │   └── Or drop a JSON file in templates/ → restart needed.
        │
        └── Yes
            ├── Drop the regulated JSON template
            │   (is_regulated: true, locked_for_business_user_edit: true)
            └── AND write a hand-coded BaseTool subclass.
                → Without it, the agent is invisible to the Planner.
```

---

## Slide 27 — Key takeaway

> **A JSON template is not enough on its own to make an agent visible to the LLM.**
>
> There has to be a tool registered with the agent's name.
> - For **non-regulated** templates, `DynamicSubAgentTool` is auto-created.
> - For **regulated** templates, you must hand-write a `BaseTool` subclass and register it.
>
> The agent registry and the tool registry are different things. The LLM only ever sees the tool registry.

If you remember nothing else, remember that.

---

## Slide 28 — Further reading

| Doc | When to read |
|---|---|
| `backend/docs/sub_agents.md` | High-level overview + locked principles. |
| `backend/docs/extending_tools_and_agents.md` | Full authoring guide — DSLs, regulated path, walkthroughs. |
| `backend/docs/architecture.md` | Orchestrator graph + Presenter engine. |
| `backend/docs/transfer_flow.md` | Worked example: the Transfer Money sub-agent. |
| `backend/docs/widgets.md` | Widget catalog + Presenter rules. |
| `backend/docs/deploy_runbook.md` | Migrations, backup/restore, agent template imports. |

Source-of-truth file per DSL:
- Predicates → `app/agents/predicates.py` (module docstring).
- `{{var}}` substitution → `app/utils/templates.py`.
- Per-node schemas → `app/agents/nodes/*.py` docstrings.

---

## Slide 29 — Q & A

Prompts to spark discussion:

- "When would you reach for the **Context tab** vs **`knowledge_search`**?"
- "Why does the **escape classifier** run before `parse_node`?"
- "What's the failure mode if you forget the hand-coded `BaseTool` for a regulated agent?"
- "Why is `tool_call_node.post_write` restricted to a **flat dict**?"
- "Why does the Planner not run on **resume** from an `interrupt_node`?"

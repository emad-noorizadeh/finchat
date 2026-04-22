# Transfer flow — end-to-end

How a money-transfer request travels from a user's chat message or voice utterance all the way to a committed transaction. This doc names every moving part, shows the state that flows between them, and highlights the governance / error-handling contracts.

---

## 1. Actors

Humans and external systems that interact with the Transfer flow. The internal code pieces are covered in §2 (*Components*).

| Actor | Who | How they engage with this flow |
|---|---|---|
| End user (chat) | Customer typing in the chat UI | Triggers the flow by asking to transfer; drives the `TransferForm` widget (Continue → Confirm → done) |
| End user (voice) | Customer in a voice session | Same intent, but interacts via spoken dialogue — prompted turn-by-turn by `interrupt_node`s |
| Business user | Non-engineer designing non-regulated sub-agents in the Agent Builder | Can author / edit / deploy *non-regulated* templates; the Transfer templates are locked to them (🔒 banner) |
| Engineer | Backend/frontend developer | Owns the regulated Transfer templates (edits `*.json` + PR), adds new actions on `TransferOpsTool`, changes `TransferService` or widget behaviour |
| Bank back-end | Upstream system (mocked in dev by `api_data/transfer/*.json`) | Serves `get_details`, `validate`, `submit`; returns validation ids and confirmation numbers |

---

## 2. Components

The internal code pieces that collaborate to satisfy the actors above.

| Layer | Component | Purpose |
|---|---|---|
| Main orchestrator | `planner` (LLM) in `app/agent/graph.py` | Sees all tools, decides to call `transfer_money` |
| Main orchestrator | `tool_execute` node in `app/agent/nodes.py` | Invokes the `TransferAgentTool`, persists the emitted widget |
| Entry tool | `TransferAgentTool` in `app/tools/transfer_tool.py` | Planner-callable wrapper that drives the sub-agent graph |
| Sub-agent | Compiled LangGraph from `app/agents/templates/transfer_money.{chat,voice}.json` | Deterministic state machine that gathers intent and produces a widget or a glass response |
| Domain tool | `TransferOpsTool` (AgentTool) in `app/tools/transfer_ops.py` | Single namespace exposing `get_details`, `resolve_account`, `validate`, `submit`, etc. as declared actions |
| Service | `TransferService` in `app/services/transfer_service.py` | Real API interactions (or mock-file reads in dev) |
| Widget | `TransferForm.jsx` in `frontend/src/components/widgets/` | Three-stage React component: form → review → completed |
| Widget handler | `_handle_transfer_validate` / `_handle_transfer_submit` in `app/widgets/actions.py` | Server-side handlers for the widget's `validate` and `submit` actions |

---

## 3. Top-level shape

```
User message
   │
   ▼
Planner LLM (main orchestrator)   ─── decides to call transfer_money(message=…)
   │
   ▼
TransferAgentTool.execute()       ─── picks template by channel, compiles once, drives inner graph
   │
   ▼
┌────────────────────────────── Sub-agent StateGraph ──────────────────────────────┐
│                                                                                 │
│   parse_node        ─── LLM extract: amount, from_account_hint, to_account_hint │
│        │                                                                        │
│        ▼                                                                        │
│   condition_node   (dispatch) ─── deterministic routing                         │
│       ╱ │ │ │ ╲                                                                 │
│      ▼  ▼ ▼ ▼  ▼                                                                │
│   tool_call_nodes                                                               │
│   (load_details, resolve_from, resolve_to, validate, submit) ─── → dispatch     │
│   interrupt_nodes (voice only) ─── pause for user reply                         │
│        │                                                                        │
│        ▼                                                                        │
│   response_node    ─── terminal: widget | glass | text                          │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
   │
   ▼
ToolResult returned to main orchestrator
   │
   ├─ widget  → persisted as WidgetInstance, dispatched to UI via SSE
   ├─ glass   → final response for voice client
   └─ text    → paraphrased back by Planner LLM (rarely used)
```

---

## 4. Chat flow — step by step

Given user message `"transfer $200 from my checking to credit card"`.

### 4.1 Main orchestrator → Transfer entry

1. `planner` node invokes the main LLM with all always-loaded tools. The LLM emits one tool call: `transfer_money(message="transfer $200 from my checking to credit card")`.
2. `tool_execute` resolves the tool from the registry → `TransferAgentTool`.
3. `TransferAgentTool.execute(input, context)`:
   - `channel = "chat"`, `user_id = "aryash"`, `session_id = "…"`.
   - Loads `transfer_money_chat` template from DB (file-seeded at first boot; see `app/agents/template_store.py`).
   - Compiles the template into a LangGraph `StateGraph` via `app/agents/template_compiler.py` (cached per process with `@lru_cache`).
   - Builds `inner_state` with `main_context = {"agent_name": "transfer_money"}` so `tool_call_node` can resolve per-agent tools.

### 4.2 Inside the sub-agent

The chat template has 8 nodes and 9 edges. Node types: `parse_node`, `condition_node`, `tool_call_node`, `response_node`.

Step-by-step trace for the example:

| Step | Node | Type | What happens |
|---|---|---|---|
| 1 | `parse_open` | parse_node | Single LLM call (variant `sub_agent`). Structured output = `{amount: 200, from_account_hint: "checking", to_account_hint: "credit card"}`. Written to `state.variables`. Call is tagged `subagent_internal` so its streaming chunks are filtered out of the chat SSE stream (`app/routers/chat.py`). |
| 2 | `dispatch` | condition_node | Evaluates predicates in declared order. First match wins. `!has(variables.transfer_details)` is true → route to `load_details`. |
| 3 | `load_details` | tool_call_node | Resolves `tool="transfer", action="get_details"` via `AGENT_TOOL_REGISTRY`. Calls `TransferOpsTool.get_details({transfer_type:"m2m"}, {user_id:"aryash"})`. That hits `TransferService.get_transfer_details`, reads `api_data/transfer/aryash/transfer_m2m.json`, returns `{sourceAccounts: [...], destinationAccounts: [...]}`. Written to `state.variables.transfer_details`. |
| 4 | `dispatch` | condition_node | `has(from_account_hint) && !has(from_account)` is true → route to `resolve_from`. |
| 5 | `resolve_from` | tool_call_node | Calls `TransferOpsTool.resolve_account({hint:"checking", candidates:[sourceAccounts…]}, ctx)`. Matches on `offeringVariant=="CK"` → returns the Checking account dict. Written to `variables.from_account`. |
| 6 | `dispatch` | condition_node | `has(to_account_hint) && !has(to_account)` is true → route to `resolve_to`. |
| 7 | `resolve_to` | tool_call_node | Same pattern with `hint="credit card"` against `destinationAccounts`. Matches `offeringVariant=="CC"` → Cash Rewards Credit Card. Written to `variables.to_account`. |
| 8 | `dispatch` | condition_node | All hints resolved; default edge (`true`) wins → route to `response_form`. |
| 9 | `response_form` | response_node | `return_mode=widget`. The `data_template` is resolved: `{amount, from_account, to_account, from_account_hint, to_account_hint, source_options, target_options}`. Writes `_response_widget` to state.variables and sets `_terminal=true`. Graph ends. |

### 4.3 Main orchestrator → widget persistence

1. `TransferAgentTool.execute` reads the terminal state, maps it to `ToolResult(widget=…, to_llm=widget_to_llm(widget))`.
2. `tool_execute` in `app/agent/nodes.py`:
   - Stamps `metadata.user_id` + `metadata.session_id` onto the widget.
   - Creates a `WidgetInstance` row via `WidgetService.create_instance` (table `widget_instances`).
   - Dispatches a custom event `widget` with `{...widget, instance_id, status:"pending"}`.
   - Sets `terminated=True` so `should_continue` routes to `end`.
3. Chat SSE stream in `app/routers/chat.py` forwards the widget event to the browser.

### 4.4 Widget lifecycle (client side)

`TransferForm.jsx` is a 3-stage state machine, driven by `data._stage` and `data.confirmation_id`:

```
form ──(Continue → POST /widgets/{id}/action action_id=validate)──▶ review
review ──(Confirm & transfer → POST … action_id=submit)──▶ completed
review ──(Back → POST … action_id=back)──▶ form
form ──(Cancel → POST … action_id=cancel)──▶ (status=dismissed)
```

- **Form stage** — displays pre-filled `amount`, `from_account`, `to_account` dropdowns seeded from the resolved accounts (falls back to client-side `matchHint` against `source_options`/`target_options` if the backend didn't resolve). User can override any field.
- **Review stage** — on `validate` action, `_handle_transfer_validate` calls `TransferService.validate_transfer`. On success, the widget's data is updated with `validation_result` + `_stage="review"`. The client re-renders showing the review card with amount, from/to labels, posting date, and any disclaimer codes humanised via `DISCLAIMER_CATALOG`.
- **Completed stage** — on `submit` action, `_handle_transfer_submit` requires the stored `_validation_id`, calls `TransferService.submit_transfer`. On success, data is updated with `confirmation_id`, `effective_date`, `_stage="completed"`. The green confirmation card renders.
- **Back** clears `validation_result` and `_stage=form`. **Cancel** sets `status=dismissed` and greys the card out.

Widget-action handlers live entirely server-side (`app/widgets/actions.py`); no sub-agent re-invocation happens.

---

## 5. Voice flow — step by step

Voice can't use a widget — it's an interrupt-driven dialogue. Template: `transfer_money.voice.json` (16 nodes, 22 edges).

### 5.1 Driver pattern

The voice sub-agent pauses via a custom mechanism, not LangGraph's native `interrupt()`. `interrupt_node` sets `variables._pending_interrupt_payload` and the compiler routes every interrupt_node to `END`. `TransferAgentTool.execute` detects the flag, translates it into an **outer** LangGraph `interrupt(payload)` so the main orchestrator pauses too. On resume, the driver re-runs the inner graph from entry, accumulating messages + variables in a process-local `_INNER_STATE` dict keyed by `thread_id = f"{session_id}_{tool}_{channel}"`.

Why not a nested checkpointer? See `app/agents/nodes/interrupt_node.py` doc comment: nested checkpointers across parallel runs create replay ambiguity. A stateless "restart the inner graph each outer turn, feed it the accumulated state" model is simpler and robust against process restarts (inner variables survive in DB-free memory only — this is an acceptable Phase-1 tradeoff).

### 5.2 Typical voice dialogue

User turns over multiple outer interrupts. Dispatch predicates decide what to do next based on what's been gathered so far.

| Turn | User says | Sub-agent does | Outer interrupt payload |
|---|---|---|---|
| 1 | "Transfer two hundred" | parse extracts amount=200; dispatch: `!has(transfer_details)` → load_details; then `!has(from_account_hint)` → **prompt_from** sets pending_interrupt | "Which account should the money come from?" |
| 2 | "Checking" | parse extracts from_account_hint="checking"; dispatch → resolve_from → dispatch → `!has(to_account_hint)` → **prompt_to** | "Where should the money go?" |
| 3 | "Credit card" | parse extracts to_account_hint; dispatch → resolve_to → dispatch → `has(amount) && has(from_account) && has(to_account) && !has(validation_result)` → **validate** → dispatch → `has(validation_result) && !has(confirmed)` → **prompt_confirm** | "Transfer $200 from Checking to Cash Rewards Credit Card. Shall I go ahead?" |
| 4 | "Yes" | parse extracts confirmed=true; dispatch → `has(validation_result) && confirmed==true` → **submit** → dispatch → `has(submit_result.confirmation_id)` → **response_completed** (glass) | (terminal) |

Final `ToolResult(glass="Done. I transferred $200 …", final=True)`. `tool_execute` dispatches it as `final_response`; voice client speaks it.

### 5.3 Voice safety guards

- **Escape classifier** (`app/agents/escape.py`): every resume utterance runs through `classify_escape` before the parse node sees it. If it's `abort` or `topic_change`, `_escape_kind` is written and the dispatcher's runtime-injected priority-0 edge routes to `response_escape`.
- **Retry exhaustion**: parse_node tracks `parse_retry_count[last_prompted_slot]`. After 3 consecutive non-advancing attempts on the same slot, `retry_exhausted_for_slot` is set and the dispatcher routes to `response_retry_exhausted`.
- **Explicit early-fail edges**: `transfer_details.status == 'ERROR'`, `validation_result.status == 'ERROR'`, `submit_result.status == 'ERROR'` each route directly to `response_failed` instead of cascading.

---

## 6. Tool architecture — the unified `transfer` tool

Every sub-agent tool_call_node addresses one **tool** + one **action**. The chat graph's `load_details` node says:

```json
{"tool": "transfer", "action": "get_details", "params": {"transfer_type": "m2m"}, "output_var": "transfer_details"}
```

### 6.1 `AgentTool` base (`app/tools/agent_tool.py`)

Pattern:

```python
class TransferOpsTool(AgentTool):
    name = "transfer"
    agent_name = "transfer_money"   # scoped to this sub-agent
    description = "Transfer operations…"
    scope = "sub_agent"

    @action("get_details", description=..., params_schema=..., output_schema=...)
    async def get_details(self, params, context): ...

    @action("resolve_account", ...)
    async def resolve_account(self, params, context): ...

    # validate, submit, get_pair, get_options
```

Registration:

```python
register_agent_tool(TransferOpsTool())
# → AGENT_TOOL_REGISTRY[("transfer_money", "transfer")] = tool
```

Lookup is agent-scoped first, then falls back to the `agent_name=""` global bucket. `tool_call_node` passes `state.main_context.agent_name` into the lookup.

### 6.2 Action contract

Each action receives `(params: dict, context: dict) -> dict | ERROR_dict`. `params` are templated values from the graph (so `{{variables.amount}}` becomes the actual number). `context` carries `user_id`, `session_id`, `channel`.

Return shapes:
- **Success** — domain dict (e.g., `{sourceAccounts: [...], destinationAccounts: [...]}`).
- **Failure** — ERROR dict: `{status: "ERROR", error_category, error, user_facing_message}`. Categories: `validation`, `policy`, `auth`, `transient`, `system`.

The dispatcher distinguishes success from ERROR via predicates like `has(variables.X) && variables.X.status == 'ERROR'`.

### 6.3 `TransferService` methods (used by the actions)

| Action | Service call | Mock data file read |
|---|---|---|
| `get_details` | `get_transfer_details(user_id, TransferType.M2M)` | `api_data/transfer/{user}/transfer_m2m.json` → key `transfer_m2m_details` |
| `get_pair` | `get_transfer_pair(user_id, source_id, tt)` | key `transfer_m2m_transfer_pair` |
| `get_options` | `get_transfer_options(...)` | key `transfer_m2m_transferOptions` |
| `validate` | `validate_transfer(...)` | key `transfer_m2m_validate`; injects `_validation_id` + `_status=READY_TO_SUBMIT` |
| `submit` | `submit_transfer(..., validation_id=...)` | returns `{status: "COMPLETED", confirmation_id, effective_date}` |
| `resolve_account` | (pure Python fuzzy match — no service call) | — |

---

## 7. State + persistence

### 7.1 Sub-agent state (`app/agents/state.py:SubAgentState`)

```python
messages: list                # LangGraph Annotated add_messages
user_id: str
session_id: str
channel: str                   # "chat" | "voice"
main_context: dict             # {agent_name: "transfer_money"}
variables: dict                # scratchpad — parse writes here, tool_call writes here
_terminal: bool                # true after a response_node has run
parse_retry_count: dict        # {slot_name: int}
last_prompted_slot: str | None
retry_exhausted_for_slot: str | None
```

Variables populated during a Transfer chat turn:
- `amount` — number from parse
- `from_account_hint` / `to_account_hint` — strings from parse
- `transfer_details` — dict from `transfer.get_details`
- `from_account` / `to_account` — account dicts from `transfer.resolve_account`
- `_response_widget` — dict built by `response_form` (consumed by the driver, not the graph)
- `_return_mode` = `"widget"` — set by the response_node
- `_escape_kind`, `_escape_hint`, `_escape_intent` — set by escape classifier on abort

### 7.2 Widget persistence (`app/services/widget_service.py`)

`WidgetInstance` row (`widget_instances` table) carries:
- `id`, `session_id`, `widget_type="transfer_form"`, `status`, `title`, `data`, `extra_data`.
- `data` is the widget's full payload. Grows across actions: initial `{amount, from_account, source_options, …}`, then `validation_result + _stage="review"`, then `confirmation_id + _stage="completed"`.
- `extra_data.user_id` + `.session_id` — stamped at creation so the action handlers can resolve the user (via `_resolve_user_id` in `app/widgets/actions.py`, which also falls back to ChatSession lookup for older rows).

### 7.3 Sub-agent template store (`app/agents/template_store.py`)

- SQL table `sub_agent_templates` — rows keyed by `name` (e.g. `transfer_money_chat`), grouped by `agent_name` (`transfer_money`).
- On first boot `seed_from_files()` imports every `*.json` template from `app/agents/templates/` as `source="seed", status="deployed"`.
- Subsequent file edits (e.g. change the `parse_open` prompt): `seed_from_files` detects a hash diff and re-syncs seed rows. User-authored (`source="user"`) rows are never overwritten.
- `locked_for_business_user_edit=true` templates reject user edits via PermissionError → HTTP 403.

---

## 8. Governance

- `is_regulated=true` — Transfer templates are marked regulated. The loader enforces:
  - `response_node.return_mode != "to_presenter"` (no slot exfiltration to the main orchestrator).
  - `llm_node.output_schema` must be set (no free-form LLM).
- `locked_for_business_user_edit=true` — write endpoints `POST/PUT/DELETE /api/agents/...` return 403. The Agent Builder UI shows a 🔒 banner and disables Save buttons.
- Seed files in `app/agents/templates/` are the source of truth for regulated flows. Changes go through PR + code review. On merge + deploy, the backend's `seed_from_files` re-sync rewrites the DB row automatically.

---

## 9. Error handling — failure modes

| Failure | Where detected | How surfaced |
|---|---|---|
| User is not eligible for m2m | `TransferService.get_transfer_details` returns `{error: ..., eligible: false}` | `TransferOpsTool.get_details` wraps it into an ERROR dict; dispatcher sees `transfer_details.status == 'ERROR'` → `response_failed` (generic text) |
| Hint doesn't match any account | `TransferOpsTool.resolve_account` returns ERROR | dispatcher sees `from_account` is truthy (ERROR dict), skips resolve again; widget receives ERROR dict and renders empty dropdown so user picks manually |
| Bank rejects validation | `TransferService.validate_transfer` returns response without `_validation_id` | Widget handler `_handle_transfer_validate` writes `data.submit_error = "The bank didn't approve…"`; widget re-renders in form stage showing the error |
| Submit service call crashes | `submit_transfer` raises | Widget handler writes `submit_error` + updates status to `failed`; widget renders red "Failed" banner |
| User aborts mid-voice | Escape classifier on resume | `_escape_kind="abort"` → runtime priority-0 edge → `response_escape` (glass "Okay, leaving the transfer.") |
| Parse fails 3x on same slot | `parse_node._apply_retry_tracking` | `retry_exhausted_for_slot` set → dispatcher → `response_retry_exhausted` |
| No tool_call_node tool_caller bound | Inner graph with misconfigured template | `tool_call_node` writes ERROR dict with `user_facing_message="Internal configuration error."` — rare; indicates a code bug |

---

## 10. Where to look — file index

```
backend/app/
├── agent/                        # Main orchestrator
│   ├── graph.py                  # build_agent_graph
│   ├── nodes.py                  # planner, tool_execute, should_continue
│   └── state.py                  # AgentState
├── agents/
│   ├── escape.py                 # abort / topic_change classifier
│   ├── nodes/
│   │   ├── parse_node.py         # regex + llm modes
│   │   ├── condition_node.py     # pass-through (routing via compiler edges)
│   │   ├── tool_call_node.py     # AgentTool dispatch
│   │   ├── interrupt_node.py     # sets _pending_interrupt_payload, compiler routes to END
│   │   ├── llm_node.py
│   │   ├── tool_node.py
│   │   └── response_node.py      # 4 return_modes
│   ├── predicates.py             # DSL compiler (has, is_empty, ==, !=, <, >, &&, ||, !)
│   ├── runtime.py                # per-thread accumulated inner state (voice driver)
│   ├── state.py                  # SubAgentState TypedDict
│   ├── template_compiler.py      # LoadedTemplate → CompiledStateGraph
│   ├── template_loader.py        # validate + load
│   ├── template_store.py         # DB adapter + seed re-sync
│   └── templates/
│       ├── transfer_money.chat.json
│       └── transfer_money.voice.json
├── routers/
│   ├── agents.py                 # GET/POST/PUT/DELETE /api/agents + patterns + deploy/disable
│   ├── chat.py                   # SSE stream; filters subagent_internal events
│   └── widgets.py                # POST /api/widgets/{id}/action
├── services/
│   ├── transfer_service.py       # M2M / CC / Zelle — reads api_data/transfer/*.json
│   └── widget_service.py         # WidgetInstance CRUD
├── tools/
│   ├── agent_tool.py             # AgentTool base, @action, AGENT_TOOL_REGISTRY
│   ├── transfer_ops.py           # TransferOpsTool — 6 actions
│   └── transfer_tool.py          # TransferAgentTool — planner entry
├── widgets/
│   ├── actions.py                # _handle_transfer_validate, _handle_transfer_submit, _handle_transfer_back
│   └── builders.py               # transfer_form_widget (catalog entry)
└── models/
    ├── sub_agent_template.py     # SubAgentTemplate SQLModel
    └── widget_instance.py        # WidgetInstance SQLModel

frontend/src/
├── components/widgets/
│   ├── TransferForm.jsx          # 3-stage widget (form → review → completed)
│   ├── WidgetRenderer.jsx        # WIDGET_MAP routing
│   └── …
├── pages/
│   └── ChatPage.jsx              # handleWidgetAction posts to /widgets/{id}/action
└── components/agents/
    └── graph/                    # Agent Builder UI (visualises the template)
```

---

## 11. Adding a new action to the Transfer tool

1. In `app/tools/transfer_ops.py`, decorate a new method:
   ```python
   @action(
       "reverse_transfer",
       description="Reverse a completed transfer within the 30-minute window.",
       params_schema={"type": "object", "properties": {"confirmation_id": {"type": "string"}}, "required": ["confirmation_id"]},
   )
   async def reverse_transfer(self, params, context):
       ...
   ```
2. Reference it from a `tool_call_node` in either template:
   ```json
   {"tool": "transfer", "action": "reverse_transfer", "params": {"confirmation_id": "{{variables.submit_result.confirmation_id}}"}, "output_var": "reverse_result"}
   ```
3. The Agent Builder picks it up automatically from `GET /api/tools?agent_name=transfer_money` and renders it in the Action dropdown with its description + params schema.

---

## 12. Adding a new sub-agent

Two paths:

- **File-authored (regulated)**: drop a `*.json` into `app/agents/templates/`. On next restart, seed_from_files imports it. The UI renders read-only (lock banner) because `locked_for_business_user_edit=true`.
- **UI-authored (non-regulated)**: open the Agent Builder, "New Agent", optionally load a pattern (`confirm_then_execute` / `collect_one_slot`), design the graph, Save as Draft → Save & Deploy. Stored as `source="user"` in the DB.

Both paths share the same compile + runtime path.

---

## 13. What's deliberately out of scope

- **Agent-authored dynamic prompts** — the `parse_node.system_prompt` is static per template. There's no runtime prompt injection from the main orchestrator.
- **Voice slot-level barge-in** — the escape classifier catches abort keywords but doesn't interrupt mid-TTS; UI teams own that.
- **Reversible transactions** — the mock service always commits on `submit`. Real deployments wire this to the bank's production API behind a feature flag.
- **Cross-session widget resumption** — `_INNER_STATE` (voice driver) is per-process memory. A backend restart mid-dialogue loses context and the user starts over. Acceptable for now; future work pins inner state to the checkpointer.

# Plan: Sub-Agent Parameters (Planner-filled arguments)

**Status:** IMPLEMENTED (2026-08-12) — plan v2 executed; one design refinement found during implementation (see below); all backend tests green (162 passed), E2E scenarios verified against the live stack.
**Date:** 2026-08-11

> **Implementation refinement to §2.6:** the blanket "no parameter may write
> to any interrupt `targets_slot`" rule proved too broad — the voice
> transfer template collects `amount`/`from_account_hint`/`to_account_hint`
> via interrupts, exactly the slots parameters should pre-fill. Shipped
> rule: interrupts opt IN per node with `data.planner_fillable: true`
> (pre-filled slot ⇒ interrupt skipped); anything unmarked — confirmation
> gates in particular — stays protected. Secure default, explicit opt-in.

---

## 1. Context & Goal

### Problem

Sub-agents are exposed to the orchestrator (Planner) LLM as function-calling tools, but their schema is a single string:

```json
{"message": {"type": "string", "description": "The user's request in natural language."}}
```

(`app/tools/dynamic_sub_agent_tool.py:95-105`, `transfer_tool.py:113-123`, `refund_tool.py:85-95`)

The Planner reads the user's message, picks the agent — and throws away everything else it understood. The sub-agent then pays a **second LLM call** (`parse_node` mode `llm` → `llm_parse`, `app/agents/parsers/__init__.py:118-172`) to re-read the same raw string and extract slots. 6 of 8 seed templates enter through an LLM parse_node. Worst offenders:

- `card_browser.chat` — burns an LLM call to extract a single `intent` field the Planner effectively already decided.
- `transfer_money.*` — 5-field extraction with a ~2 KB system prompt on every entry.

### Goal

Let sub-agent templates declare **parameters** (name, type, enum, description) the way tools do. The parameters merge into the entry tool's OpenAI schema, so the Planner fills them **in the same LLM call where it picks the agent** — zero extra cost. Values seed the inner graph's `variables` (the slot scratchpad). `parse_node` then:

- **skips its LLM call entirely** when every slot it writes is already filled, or
- extracts **only the missing fields** otherwise —

**but only on the first pass of a Planner entry.** On interrupt-resume passes the parser always runs with its full schema, because (a) on `Command(resume=…)` the user's reply lands directly in the inner graph and the Planner never runs, and (b) resume replies may *correct* already-filled slots ("no, make it $30") — narrowing there would silently drop corrections. See §2.5.

### Non-goals

- No change to the inner-graph node grammar (no new node types).
- No structured *output* channel changes (ToolResult stays as-is).
- No automatic conversion of existing parse_node prompts into parameters (authors opt in per agent).
- Starter parameters in graph patterns (`GET /agents/patterns` applies only `skeleton` to `graph_definition` — `AgentBuilder.jsx:376`, `app/agents/patterns/__init__.py:20-27`). v2 candidate.

---

## 2. Design Overview

### 2.1 Parameter declaration (agent-level)

New JSON column `parameters` on `sub_agent_templates`, **agent-level** like `knowledge_collections` (stored per-row, synced across channel variants — same pattern as `_sync_knowledge_collections_to_siblings`, `app/agents/template_store.py:204-227`):

```json
{
  "properties": {
    "transfer_type": {
      "type": "string",
      "enum": ["internal", "external", "zelle"],
      "description": "Type of transfer, if the user stated or implied it."
    },
    "amount": {
      "type": "number",
      "description": "Dollar amount to transfer, if stated. Omit if not explicit."
    }
  },
  "required": [],
  "writes": {"transfer_type": "transfer_type", "amount": "amount"}
}
```

- `properties` — OpenAI-tool-style property specs. Allowed types: `string`, `number`, `integer`, `boolean` (scalar-only in v1).
- `required` — subset of property names. **Default and recommended: empty.** A "required" parameter forces the Planner to always supply a value (inviting guesses); slot-filling agents should treat everything as optional and let parse/interrupt chase what's missing. The builder UI carries this warning.
- `writes` — optional param→variable map, defaulting to identity (same convention as parse_node `writes`, `app/agents/nodes/parse_node.py:55-57`).

Parameter descriptions carry the extraction guidance that today lives in parse_node system prompts ("omit unless the user explicitly stated it" etc.).

### 2.2 Data flow

```
Builder UI / seed JSON / import
      │  parameters {properties, required, writes}
      ▼
sub_agent_templates row  ──sync──▶ sibling channel variants (upsert AND import paths)
      ▼
LoadedTemplate.parameters (validated, incl. cross-variant slot-safety checks)
      ▼
DynamicSubAgentTool.input_schema()  =  message + declared properties   ──▶ Planner LLM
      ▼  tool_call args
execute(): lenient-validate → drop invalid/empty → seed inner variables (once per tool_call)
      ▼
parse_node (planner-entry pass only): all writes-targets filled? ──yes──▶ skip LLM call
                                                                 └─no──▶ extract only missing fields
parse_node (resume pass): full parse, exactly today's behavior
```

### 2.3 Lenient validation ("drop, don't die")

The Planner may hallucinate. A helper validates each supplied arg against its declared spec; **invalid values are dropped with a log line, never an error** — the flow degrades to exactly today's parse/interrupt behavior. Rules:

- JSON-type match per declared `type`. **`bool` is checked before the numeric types** (Python `isinstance(True, int)` is `True`); `None` is always dropped.
- `enum` membership when declared.
- **Emptiness = absence**: `""`, `[]`, `{}` are dropped, matching the predicate DSL's `has()` semantics (`app/agents/predicates.py:311-315`). Without this, a Planner-supplied `""` would count as "filled" for the parse-skip while `has()` still prompts for the slot — the user's answer would be narrowed out of the parse schema and the retry counter would exhaust the flow.

No `jsonschema` dependency needed for scalar-only specs. A single `is_filled(value)` helper, shared with the parse-skip check, keeps skip semantics and `has()` semantics identical.

### 2.4 Seed-once semantics (replay-safe)

`execute()` re-runs **from the top on every LangGraph resume replay** — state was saved before `interrupt()` (`dynamic_sub_agent_tool.py:156-160`), is non-terminal, and survives in the runtime store (`app/agents/runtime.py`), so `_initial_inner_state`'s prior-state branch (`:184-188`) is hit on *every replay of the same tool call*, not only when a user returns to an abandoned flow. Naive re-seeding would resurrect stale Planner guesses into slots the user has since changed or cleared.

Therefore seeding is keyed to the **tool_call id**:

- The orchestrator's `tool_execute` passes `tc["id"]` into the tool `context` (one-line addition to the context dict at `app/agent/nodes.py:413-420`).
- `_initial_inner_state` seeds only when `variables.get("_planner_args_call_id") != tool_call_id`, then records the id (underscore-prefixed → invisible to `writes` targets, which validation restricts anyway).
- **Fresh start**: seed unconditionally.
- **True continuation** (new Planner tool_call over a prior non-terminal state — i.e., an abandoned interrupt; escape terminals `clear_inner_state`, so escapes never continue): seed **fill-empty-only** — never overwrite slots already gathered interactively. Mid-flow corrections arrive via the resume path where the Planner never runs, so overwrite logic could only ever apply stale guesses.
- **Replay of the same call**: no re-seed.

### 2.5 Parse skip is gated to the Planner-entry pass

When `_initial_inner_state` seeds, it also sets a transient `variables["_planner_seeded"] = True`. `parse_node` consumes the flag:

- Flag present → skip/narrow logic applies (and the node clears the flag in its returned update).
- Flag absent (resume passes, or agents invoked without parameters) → **full parse with the complete schema — byte-for-byte today's behavior.** Corrections at a confirmation prompt keep working; `confirmed`-style slots keep being parsed every resume.

Retry-tracking correction: when the skip path runs and `last_prompted_slot` is already filled (a continuation where new Planner args satisfied the pending slot), the slot is treated as **progress** (added to `written` before `_apply_retry_tracking`) — otherwise the counter would wrongly increment toward `retry_exhausted_for_slot` (`parse_node.py:152-155`).

### 2.6 Slot-safety validation (confirmation bypass prevention)

Nothing may let the Planner pre-fill a human-confirmation gate (e.g. `transfer_money.voice` submits on `confirmed == true`, `templates/transfer_money.voice.json:246-249`). Validation rejects parameter `writes` that target:

- any `interrupt_node.data.targets_slot` in **any channel variant** of the agent (cross-variant check at upsert/import time, where sibling rows are available; single-graph check in the loader);
- any node `output_var` in any variant (those are computed outputs — a seeded scalar would falsify `!has(variables.X)` guards and skip data loads, e.g. `transfer_details` at `templates/transfer_money.chat.json:289-294`);
- `_`-prefixed names (reserved: `_return_mode`, `_pending_interrupt_payload`, …);
- the property name `message` (would clobber the base field in the merged schema).

---

## 3. Backend Changes

### 3.1 Model + migration

**`app/models/sub_agent_template.py`**
- Add `parameters: dict = Field(default_factory=dict, sa_column=Column(JSON, default={}))` with a docstring comment mirroring `knowledge_collections` ("agent-level, synced across channel variants").

**`backend/migrations/versions/<rev>_add_parameters_to_sub_agent_templates.py`**
- New alembic revision, `down_revision = 'c5d8e2a1f9b7'` (current head). Copy that revision's shape exactly: `nullable=False` + `server_default='{}'` (not nullable — matches the knowledge_collections precedent).
- Boot path is `run_migrations()` (`app/main.py`); the legacy `_ensure_sub_agent_template_columns` in `app/database.py` is deprecated (it doesn't cover `knowledge_collections` either) — **no `database.py` change.**

### 3.2 Loader + store

**`app/agents/template_loader.py`**
- `LoadedTemplate`: add frozen `parameters: dict` field.
- New `_validate_parameters(raw)` in `load_template`:
  - `properties` is a dict; each spec has `type` ∈ {string, number, integer, boolean}; `enum` (if present) is a non-empty list of scalars matching `type`; `description` is a string; property name ≠ `message`.
  - `required` ⊆ property names.
  - `writes` keys ⊆ property names; values are non-empty, non-`_`-prefixed strings; within this graph, no write targets an `interrupt_node.targets_slot` or a node `output_var` (§2.6).
  - Raise `TemplateValidationError` with a field-specific message (surfaces as HTTP 400 in the builder).
- **Hash stability**: `LoadedTemplate.hash` hashes the whole raw dict (`template_loader.py:100-102`), so `_row_to_raw` must **omit the `parameters` key when empty** — existing templates keep their current hash and the backward-compat golden tests stay honest.

**`app/agents/template_store.py`**
- `_row_to_raw`: include `parameters` only when non-empty (see hash note above).
- **`_row_values_from_raw` (`:369-393`): add `parameters`.** This function — not `upsert_template` — builds row values for `bootstrap_from_files` (`:293`) and `import_template_file` (`:334`). Without it, seed bootstrap and the admin import endpoint (the *only* edit path for locked transfer/refund rows) silently drop parameters.
- `upsert_template(...)`: new kwarg `parameters: dict | None = None`, persisted like `knowledge_collections`.
- Generalize sibling sync → `_sync_agent_level_fields_to_siblings(db, agent_name, exclude_name, {"knowledge_collections": ..., "parameters": ...})`, and **call it from `import_template_file` too** — today import writes one row with no sync, which would leave e.g. the voice variant without parameters and make refresh aggregation row-order-dependent.
- Cross-variant slot-safety check (§2.6) runs at upsert/import time against all sibling graphs, since the loader only sees one graph.

### 3.3 API — `app/routers/agents.py`

- `AgentUpsertRequest`: add `parameters: dict = {}` (`:241-258`).
- `_build_raw`: include `parameters` (omit when empty) (`:266-289`).
- `create_agent` / `update_agent`: pass `parameters=req.parameters` to `upsert_template`.
- `_row_to_variant` (`:95`) and `get_agent_variant` (`:199`): include `parameters` so the builder can hydrate.
- `export_template` (`:160`): include `parameters` in the flat seed doc.
- `import_template_json` (`:399`) + `admin_import_file` (`:447`): no direct change needed once `_row_values_from_raw` and import-path sync (§3.2) land — verify with the round-trip test.

### 3.4 Entry tools

**New shared helper — `app/tools/sub_agent_params.py`**
```python
def is_filled(value) -> bool:
    """Shared emptiness rule — matches predicates.has(): None, "", [], {} are unfilled."""

def merge_input_schema(base_message_description: str, parameters: dict) -> dict:
    """message field + declared properties; required = ["message"] + declared required.
    Empty parameters → byte-identical to today's one-field schema."""

def filter_valid_args(args: dict, parameters: dict, *, agent_name: str) -> dict:
    """Only declared args passing type/enum checks; bool checked before int/number;
    drops None and empty values (§2.3).
    Drops log: [sub_agent_arg_dropped.v1] agent=%s param=%s reason=%s"""

def seed_variables(variables: dict, valid_args: dict, writes: dict, *, fill_empty_only: bool) -> dict:
    """Apply writes map; fill-empty-only respects is_filled()."""
```

**`app/tools/dynamic_sub_agent_tool.py`**
- `__init__`: accept `parameters: dict`; store on instance.
- `input_schema()`: `merge_input_schema(...)`.
- `execute()`: `valid = filter_valid_args(input, self.parameters, agent_name=self.name)`; pass into `_initial_inner_state` along with `context.get("tool_call_id")`.
- `_initial_inner_state()`: seed-once-per-tool-call logic (§2.4); set `variables["_planner_seeded"]` and `variables["_planner_args_call_id"]`; also `main_context["planner_args"] = valid` (observability + predicate access via `main_context.X`, `app/agents/predicates.py:283-284`).
- `refresh_dynamic_sub_agent_tools()`: read `parameters` from rows (synced, so take the first non-empty), pass to the constructor.
- **Fix (pre-existing bug, load-bearing for the edit→test loop):** `_compiled_for` is `@lru_cache` and never invalidated (`dynamic_sub_agent_tool.py:50`, `transfer_tool.py:53`, `refund_tool.py:38` — all read templates from the DB). Add `cache_clear()` for all three in `_refresh_registry` (`app/routers/agents.py:292`). Note: registries and inner-state stores are process-local by existing design (`run.py` runs a single worker); this fix inherits that assumption — documented, not solved here.

**`app/agent/nodes.py` (orchestrator)**
- Pass `tc["id"]` as `tool_call_id` in the tool context dict (`:413-420`) — one line, enables seed-once.
- **Deferred-schema refresh:** `enrich` carries previously discovered deferred tools' schemas verbatim from session state (`:268-274`), so a parameters edit would be invisible to any session that already discovered the tool — for the rest of the session. Fix: re-serialize each carried deferred tool from the live registry (`get_tool(name).to_openai_schema()`) each turn, falling back to the stored dict if the tool vanished. Also fixes stale descriptions generally, and makes acceptance criterion 6 actually true for deferred agents.

**`app/tools/transfer_tool.py` / `app/tools/refund_tool.py`** (regulated, hand-coded)
- `input_schema()`: look up the template (`template_for_agent(self.name, "chat")` — parameters are agent-level) and `merge_input_schema(...)`. DB read per call is acceptable; revisit only if profiling says otherwise.
- `execute()` / `_initial_inner_state()`: same seed-once logic as the dynamic tool (share the helper).
- **Update `description()` text in the same change**: `transfer_tool.py:109-110` currently instructs "Do NOT pre-fill account or payee details" — it would sit next to the new parameter schema and fight it. Reword to: pass verbatim `message` plus any parameters the user explicitly stated.
- Regulated rows are locked for user edit, so their `parameters` change via seed-file PR + `POST /agents/admin/import-file/{filename}` — which works because of the `_row_values_from_raw` fix (§3.2).

### 3.5 parse_node — skip / narrow (gated)

**`app/agents/nodes/parse_node.py`**
- llm handler, only when `variables.get("_planner_seeded")` (§2.5):
  - `missing = [f for f, var in writes.items() if not is_filled(variables.get(var))]`
  - `missing == []` → skip the LLM call; log `[parse_node_skipped.v1] agent=%s` (the E2E assertion hook); if `last_prompted_slot` is filled, count it as progress before `_apply_retry_tracking` (§2.5).
  - else → `llm_parse` with `output_schema` narrowed to the missing fields.
  - Clear `_planner_seeded` in the returned update either way.
- No flag → full parse, unchanged code path.
- regex handler: same gated skip.
- New optional node data flag `always_run: true` to opt out of skipping even on the seeded pass. Document both in the node-data docstring (`:13-26`).

### 3.6 Planner prompt — `app/services/enrichment.py`

- Extend the action-tool rules (`:128-134`): keep "pass the user's full request verbatim as `message`", add: *"Also fill any other parameters of the tool that the user explicitly stated or clearly implied. Omit parameters the user did not state — never guess or invent values."*

### 3.7 Seed templates (reference examples)

- `app/agents/templates/card_browser.chat.json` — add an `intent` parameter mirroring its parse enum. Note: a Planner-supplied `intent` makes the template's decline-guard branch (`:73`) rarely taken — acceptable; the parameter description instructs the Planner to omit on ambiguity, which falls back to parse.
- `app/agents/templates/transfer_money.chat.json` + `.voice.json` — add the chat parse fields as parameters **except** `confirmed` (voice's confirmation slot — validation would reject it anyway, §2.6). Consequence to state honestly: voice transfer can never fully skip its parse (it parses `confirmed` on resume passes by design), so the "zero parse calls" outcome is chat-only for transfer; voice still benefits from narrowing on entry.
- Existing DBs are not re-seeded (bootstrap is first-boot-only); apply via the admin import endpoint or builder UI during verification.

---

## 4. Frontend Changes

All in `frontend/src/`. The builder is a single controlled form in `components/agents/AgentBuilder.jsx` (`form` state `:199-216`, `setField` `:261`, `buildPayload()` `:290-309`) — no Zustand involvement.

### 4.1 New "Parameters" tab in the builder left panel

**`components/agents/AgentBuilder.jsx`**
- Add `'parameters'` to the tab list (`:452-464`) → 6 tabs; bump `leftWidth` initial 384 → ~440 (comment at `:162`: 384 "fits 5 tabs without wrapping").
- Form state: `parameters: { properties: {}, required: [], writes: {} }` in the initial form (`:199-216`); hydrate in the edit-load `setForm` (`:231-255`) from `GET /agents/{agentName}/{channel}` (returns `parameters` after §3.3); include in `buildPayload()`.

**New `components/agents/ParametersTab.jsx`** — row-list editor following the `ParallelToolsNodeEditor` add/remove/update-row pattern (`graph/NodePropertiesPanel.jsx:512-639`) and the `ParamField` control-per-type pattern (`:705-783`):
- One row per parameter: `name` (text, slug-validated, `message` rejected inline), `type` (select: string/number/integer/boolean), `enum` (comma-separated, shown for string/number/integer), `description` (textarea — helper copy: *this is what the orchestrator LLM reads; say when to omit*), `required` (checkbox with the "leave off unless truly mandatory" warning), `writes to variable` (text, placeholder = name).
- `+ Parameter` / per-row `✕`, mirroring the `extractors` editor (`:254-289`).
- **Raw JSON escape hatch**: toggle swapping the rows for a `JsonField` (`:196-224`) bound to the whole `parameters` object (mirrors `ParamsEditor`'s fallback) so hand-authored schemas are never lossy.
- Serialize rows ⇄ `{properties, required, writes}` (writes entry omitted when identical to name). Server-side validation errors (400 from slot-safety checks) surface through the existing save-error path.

### 4.2 Parameters visible as variables in node editors

`useUpstreamVariables` (`graph/NodePropertiesPanel.jsx:958-1004`) only surfaces variables written by upstream *nodes*; Planner-seeded parameters exist from entry. Pass the form's `parameters` down (AgentBuilder → NodePropertiesPanel prop) and merge its writes-targets into `slotVars` so `VariablesPanel` (`:1011-1055`) and the predicate tester offer `{{variables.<param>}}` tokens.

### 4.3 parse_node editor + prompts overview hints

- `ParseNodeEditor` (`NodePropertiesPanel.jsx:229-318`): info line when the agent declares parameters — *"Slots already filled by agent parameters are skipped on entry; this parser still runs in full on interrupt replies"* — plus an `always_run` `CheckboxField`.
- `PromptsOverview` (`AgentBuilder.jsx:46-104`, prompt tab): badge parse_node entries whose writes-targets are all covered by parameters ("may skip on entry") so authors reading prompts know they might not run.

### 4.4 Read-only verification surfaces (no code change expected)

- **ToolsPage** (`pages/ToolsPage.jsx:209-240`) renders `GET /tools/{name}` → `input_schema.properties`; the merged schema appears automatically. Primary UI assertion point for E2E.
- **Import/Export** (`pages/AgentsPage.jsx:23-61`) round-trips whatever the backend emits; works once §3.2/§3.3 land.

---

## 5. Tests

### 5.1 Backend (pytest, `backend/tests/`)

**Infrastructure prerequisite (own work item):** no `conftest.py` exists and no current test touches the DB — all suites are pure loaders + mocks (e.g. `test_knowledge_collections.py`). Store/refresh tests need a fixture that creates a temp SQLite engine and patches it into `app.agents.template_store` (it imports `engine` **by value** at `template_store.py:22` — patch the module attribute, not `app.database.engine`) plus `get_session_context`. Budget this explicitly.

New `test_sub_agent_parameters.py`:

| # | Test | Asserts |
|---|---|---|
| 1 | Loader validation | valid shape loads; bad type, enum/type mismatch, `writes`→unknown property, `writes`→`_reserved`, `writes`→`targets_slot`, `writes`→`output_var`, property named `message`, `required` ⊄ properties → `TemplateValidationError` |
| 2 | Schema merge | empty params == today's exact one-field schema (golden); with params → message + properties |
| 3 | Lenient filtering | wrong type dropped; enum violation dropped; undeclared keys dropped; `True` rejected for integer/number; `None` dropped; `""`/`[]`/`{}` dropped; valid values kept |
| 4 | Seeding — fresh | args land in `variables` via `writes`; `main_context.planner_args`, `_planner_seeded`, `_planner_args_call_id` set |
| 5 | Seeding — continuation vs replay | same call id → no re-seed; new call id over prior state → fill-empty-only (filled slot untouched, emptied slot NOT resurrected by same-id replay) |
| 6 | parse skip (seeded pass) | all targets filled → no `llm_parse` (mock), `[parse_node_skipped.v1]` logged, `_planner_seeded` cleared, filled `last_prompted_slot` counted as progress (retry counter cleared, not incremented) |
| 7 | parse narrowing (seeded pass) | partial → `llm_parse` called with only missing fields |
| 8 | parse on resume pass | no `_planner_seeded` → full schema parse even when all slots filled (correction path preserved) |
| 9 | `always_run` | full parse even on seeded pass |
| 10 | Store round-trip + sync | upsert with parameters → row → `LoadedTemplate.parameters`; sibling synced; **import path** also syncs; `_row_values_from_raw` carries parameters (bootstrap + admin import) |
| 11 | Export/import round-trip | export doc contains `parameters`; re-import preserves them |
| 12 | Registry refresh | edited parameters reflected in `input_schema()` without restart; `_compiled_for` caches cleared; deferred-schema re-serialization picks up the change |
| 13 | Backward compat | template without `parameters`: schema, hash, seeding, parse behavior identical (golden; hash unchanged because empty params are omitted from raw) |

Existing suites must pass unmodified.

### 5.2 UI / E2E — executed by Claude via Chrome MCP

Both servers up (`backend: python run.py` :6000, `frontend: npm run dev` :6001; Vite proxies `/api`). Backend log tailed via Bash in parallel — UI assertions from Chrome MCP; LLM-call assertions from `[parse_node_skipped.v1]` / `[sub_agent_arg_dropped.v1]` lines and absence of `subagent_internal` parse activity for the turn. Chat E2E only (voice needs a mic; voice-specific behavior is covered by unit tests #8 and the transfer.voice narrowing case).

| # | Scenario | Steps (Chrome MCP) | Pass criteria |
|---|---|---|---|
| E1 | Author parameters | Login via profile card → `/agents` → edit `card_browser` (chat) → Parameters tab → add `intent` (string, enum, description) → Save & Deploy | 200 on `POST /agents` + deploy; reopening the builder re-hydrates the rows; raw-JSON toggle shows the same object |
| E2 | Schema visible to Planner | `/tools` → expand `card_browser` | Input Parameters lists `message` **and** `intent` with enum + description |
| E3 | Parse skipped end-to-end | `/chat` → message that triggers card_browser with explicit intent | Correct widget/response; log shows `[parse_node_skipped.v1]`; no `subagent_internal` parse call that turn |
| E4 | Partial fill (chat, widget-first) | Send "transfer $50" | Planner fills `amount`; parse log shows narrowed schema (amount absent); TransferForm widget reflects the seeded amount. (Chat transfer has no interrupt_node — interrupt coverage is voice-only, tested at unit level) |
| E5 | Continuation fill-empty-only | Start a transfer (widget shown, amount confirmed), then send "actually send it to my savings" | Planner re-calls with a new tool_call; `to_account` hint fills; previously confirmed `amount` NOT overwritten (no re-ask, widget keeps $50) |
| E6 | Hallucination resilience | Author an enum param; drive a turn likely to produce an out-of-enum value (or temporarily deploy a test agent whose enum can't match) | `[sub_agent_arg_dropped.v1]` logged; flow falls back to parse; no user-facing error |
| E7 | Export/import round-trip | `/agents` kebab → Export JSON → delete agent → Import JSON | Parameters intact in the builder after re-import; sibling variant (if any) also carries them |
| E8 | Slot-safety rejection | In the builder, add a parameter writing to a voice interrupt's `targets_slot` (e.g. `confirmed` on a copy of transfer) → Save | 400 surfaced in the builder's save-error UI; template unchanged |
| E9 | Regression | Run a no-parameters agent (e.g. `account_qa`) through chat | Behavior unchanged; no new log lines |

Record a GIF of E1→E3 (`gif_creator`) as the review artifact.

Note: the existing Playwright spec (`frontend/tests/compound_response.spec.js`) has a stale login helper and selectors (no `/login` route, no `data-testid`s) — out of scope; Chrome MCP runs against the real UI instead.

### 5.3 Verification commands

```bash
cd backend && .venv/bin/pytest tests/ -q          # full suite
cd frontend && npm run build                       # no type/bundle errors
```

---

## 6. Acceptance Criteria

1. **Zero-cost extraction**: for an agent+channel whose parameters cover all parse writes, a Planner-routed entry executes with **no** `subagent_internal` parse call (E3). Stated honestly per-channel: voice flows that parse `confirmed` on resume keep those calls by design (§3.7).
2. **Backward compatible**: agents without `parameters` → byte-identical schemas, identical hashes, identical runtime behavior; full existing suite green (5.1 #13, E9).
3. **Parser degradation, not removal**: missing/invalid/empty Planner args always fall back to parse/interrupt; no user-facing errors (5.1 #3, E6).
4. **Resume path untouched**: interrupt replies always get a full-schema parse — corrections to filled slots (including confirmations) work exactly as today (5.1 #8; design §2.5).
5. **No confirmation bypass**: parameters cannot write to interrupt target slots or node output_vars in any channel variant (5.1 #1, E8).
6. **Authorable in the UI**: create, edit, re-hydrate, export, import from the builder without hand-editing JSON (raw-JSON mode available) (E1, E7).
7. **Live without restart**: parameter edits reach the Planner on the next turn — including sessions that already discovered the tool (deferred-schema refresh) — and graph edits recompile (`_compiled_for` fix) (5.1 #12).
8. **Regulated parity**: transfer/refund parameters deploy via seed-file + admin import (`_row_values_from_raw` fix) with the same runtime semantics (5.1 #10).
9. **Docs updated**: both tutorial decks, `extending_tools_and_agents.md`, `sub_agents.md` describe the contract (and the node-type count corrected to include `parallel_tools_node`); generic naming per repo convention.

---

## 7. Implementation Order

1. **Backend core**: model + migration → loader/store (incl. `_row_values_from_raw`, import-path sync, slot-safety) → `sub_agent_params.py` helper → dynamic tool seed-once → parse_node gating → cache + deferred-schema fixes. Tests alongside each piece (conftest fixture first).
2. **API + prompt** (§3.3, §3.6) + orchestrator `tool_call_id` context line.
3. **Hand-coded tools** (transfer/refund incl. description rewrites) + seed template parameters.
4. **Frontend** (§4): ParametersTab → hydration/payload → variables surfacing → editor hints.
5. **E2E via Chrome MCP** (§5.2), fixing what it uncovers.
6. **Docs** (§6.9).

---

## 8. Adversarial Review — findings and how the plan absorbed them

An independent review pass was run against v1 of this plan with full code access. All 18 findings and their dispositions:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | HIGH | Skip/narrow ran on resume passes too — mid-flow corrections ("no, make it $30") would be silently dropped | **Design changed**: skip gated to the `_planner_seeded` entry pass; resume always full-parses (§2.5, criterion 4) |
| 2 | HIGH | `execute()` replays re-hit the continuation branch every resume; naive re-seeding resurrects stale args; escape terminals clear state so "escape and return" was a misidentified scenario | **Design changed**: seed-once keyed by `tool_call_id` (§2.4) |
| 3 | HIGH | `is None` skip check vs `has()` emptiness (`""` = absent) → deadlock: prompt loops on a slot the parser no longer extracts | **Design changed**: shared `is_filled()`; empty values dropped at validation (§2.3) |
| 4 | HIGH | A parameter writing to `confirmed` lets the Planner bypass human confirmation before an irreversible action | **Design changed**: slot-safety validation, cross-variant (§2.6, E8) |
| 5 | HIGH | `bootstrap_from_files` / `admin_import_file` build rows via `_row_values_from_raw`, which the plan missed — the regulated-agent deploy path would silently drop parameters | Added (§3.2) |
| 6 | MED | Import path didn't sibling-sync; sync bypassed per-graph validation | Added: import-path sync + cross-variant validation at store level (§3.2, §2.6) |
| 7 | MED | Skipping with `written=∅` increments the retry counter when Planner args filled the pending slot | Fixed: filled `last_prompted_slot` counts as progress (§2.5, test #6) |
| 8 | MED | Deferred tools' schemas are carried verbatim in session state — parameter edits invisible to ongoing sessions despite `cache_clear` | Added: per-turn re-serialization from the live registry (§3.4, criterion 7) |
| 9 | MED | No DB test infrastructure exists; `template_store` imports `engine` by value | Called out as an explicit work item with the correct patch target (§5.1) |
| 10 | MED | E4/E5 as written were untestable (chat transfer has no interrupts; escape clears state) | E4/E5 rewritten around widget-first chat + abandoned-continuation (§5.2) |
| 11 | MED | `transfer_tool.description()` says "Do NOT pre-fill account or payee details" — fights the new schema | Description rewrite added to §3.4 |
| 12 | MED | Agent-level parameters vs per-channel parse schemas (voice parses `confirmed`) — "zero parse calls" is chat-only for transfer | Stated honestly in §3.7 and criterion 1 |
| 13 | LOW | `isinstance(True, int)` passes numeric checks; `None` needs explicit drop | §2.3 + test #3 |
| 14 | LOW | No rejection of a `message`-named property or writes colliding with output_vars | §2.6 validation |
| 15 | LOW | Adding `parameters` to `_row_to_raw` changes every template's hash | Empty params omitted from raw; hash-stability asserted in test #13 |
| 16 | LOW | Migration details: `down_revision='c5d8e2a1f9b7'`, `nullable=False` + server_default; alembic-only confirmed correct | §3.1 |
| 17 | LOW | `PromptsOverview` unaware of skipping; patterns can't ship parameters; seeded `intent` makes card_browser's decline guard mostly dead | §4.3 badge; patterns → non-goal; card_browser note in §3.7 |
| 18 | LOW | Registries/caches are process-local; `cache_clear` is single-worker-scoped like everything else | Documented in §3.4 |

Residual risks accepted for v1: single-worker assumption (pre-existing, whole-registry-wide); Planner over-eagerness filling parameters despite prompt guidance (mitigated by per-parameter "omit unless stated" descriptions + drop-don't-die validation + full-parse resume path as the safety net); no starter parameters in graph patterns.

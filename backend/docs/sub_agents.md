# Sub-agents — template-backed, LangGraph-compiled

Every sub-agent is a **JSON template** that compiles into a **LangGraph
`StateGraph`** at first use. The main orchestrator's Planner calls the
sub-agent as a tool; the sub-agent then drives its own internal graph
deterministically (no LLM picks routing — predicates do).

This doc is the high-level overview. For the full authoring story
(building a template via the UI, the DSLs, the regulated-vs-not deploy
flow), read `extending_tools_and_agents.md`. For deploy ops, read
`deploy_runbook.md`.

Related docs:
- `extending_tools_and_agents.md` — authoring guide (UI walkthrough, DSLs, deploy)
- `architecture.md` — system-wide reference (main orchestrator + sub-agent file map)
- `deploy_runbook.md` — migrations, backup/restore, agent template imports
- `transfer_flow.md` — worked example: the Transfer Money sub-agent

## Why this shape

Widgets let chat sub-agents hide tedious form-filling under a single
widget; voice has no form. The previous LLM-driven Transfer sub-agent
relied on conversational judgment at every step (what to ask next, how
to parse the reply, when to confirm, whether to execute). That produced
malformed tool payloads, skipped confirmations, and dead loops — bad
UX even when the underlying tool was safe.

The template-backed design splits responsibility four ways:

| Layer | Role |
|---|---|
| Tool | Owns business rules, auth, limits, fraud — unchanged from the planner's perspective. |
| Template | Declares the procedure shape: nodes (parse / condition / interrupt / tool_call / llm / response), edges with predicates. |
| Sub-agent runtime | Drives the compiled `StateGraph` deterministically. Routing is by Predicate DSL, not by an LLM choosing the next step. |
| Orchestrator (Planner) | Routes user intent to sub-agents (via `tool_search` or `always_load`) and paraphrases their return values. |

## Graph-level view

```
User
  │ POST /api/chat/sessions/:id/messages
  ▼
Main orchestrator (Planner + Presenter, app/agent/graph.py)
  │ calls e.g. transfer_money or card_advisor tool
  ▼
{Sub-agent}Tool.execute  (e.g. TransferAgentTool, DynamicSubAgentTool)
  │ loads the (agent_name, channel) → template, compiles it once (cached)
  ▼
Inner StateGraph (compiled by app/agents/template_compiler.py)
  │ runs entry → … → response_node, pausing at interrupt_node for user input
  ▼
ToolResult returned to the Planner — widget, glass text, or to_orchestrator text
```

Each `interrupt_node` ends the inner graph cleanly; the outer driver
saves the inner state, re-presents the prompt to the user, then re-enters
the inner graph from the entry node with the user's reply appended and
the accumulated state restored. See `app/agents/runtime.py` for the
per-thread state registry.

## Node grammar (v4)

Eight node types, registered in `app/agents/nodes/` (see also
`parallel_tools_node` for concurrent AgentTool fan-out):

| Type | Purpose |
|---|---|
| `parse_node` | Extract values from the latest user utterance into `state.variables`. Two modes: `regex` (deterministic, uses registered parsers like `money` / `yes_no` / `last4`) or `llm` (structured output via `llm_parse`). |
| `condition_node` | Pure-Python routing. Has multiple outgoing edges, each carrying a Predicate-DSL expression. First-match-wins. **No LLM**. |
| `interrupt_node` | Pauses execution, surfaces a prompt to the user, ends the inner graph. The outer driver handles the resume. `targets_slot` lets the parse_node on the next entry know which slot the user is answering. |
| `tool_call_node` | Dispatches an `AgentTool.dispatch(action, params, context)`. `output_var` captures the tool's return into `state.variables`. Supports `post_write` for flat scalar state writes on success. |
| `llm_node` | Free-form LLM call with a system prompt + bound tool subset. Used by non-regulated sub-agents that need fuzzy interpretation (e.g. `card_advisor`'s recommendation step). |
| `tool_node` | Executes any `tool_calls` emitted by the preceding `llm_node`. No configuration — pure dispatcher. |
| `response_node` | Terminal. Four `return_mode`s: `widget` / `glass` / `to_presenter` (slot writeback) / `to_orchestrator` (text template the parent LLM paraphrases). |

A typical regulated flow: `parse_node → condition_node → (tool_call_node / interrupt_node)+ → response_node`.

## Template anatomy

File: `app/agents/templates/*.json` (seed) and the `sub_agent_templates`
DB table (runtime source of truth, populated by bootstrap or admin
import). Loaded via `app/agents/template_loader.py` → validated →
compiled by `app/agents/template_compiler.py`.

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
  "context": "# Card comparison\n\n| Card | Cashback | Annual fee | …",
  "entry_node": "parse_open",
  "nodes": [
    { "id": "parse_open", "type": "parse_node",     "data": { "mode": "llm", "system_prompt": "…", "writes": { "interest": "interest" } } },
    { "id": "ask",        "type": "interrupt_node", "data": { "prompt_template": "What matters most to you?", "targets_slot": "interest" } },
    { "id": "dispatch",   "type": "condition_node", "data": { "label": "Dispatch on interest" } },
    { "id": "rec_cash",   "type": "response_node",  "data": { "return_mode": "to_orchestrator", "text_template": "Recommend the Everyday Cash Rewards card. …" } }
  ],
  "edges": [
    { "source": "parse_open", "target": "ask",      "predicate": "!has(variables.interest)" },
    { "source": "parse_open", "target": "dispatch", "predicate": "has(variables.interest)" },
    { "source": "ask",        "target": "parse_open" },
    { "source": "dispatch",   "target": "rec_cash", "predicate": "variables.interest == \"cashback\"" }
  ]
}
```

## Planner-fillable parameters

A template may declare `"parameters"` — an agent-level field (synced across
channel variants like `knowledge_collections`) with shape
`{"properties": {name: {type, enum?, description?}}, "required": [...],
"writes": {name: variable}}` (writes defaults to identity):

- **Schema merge.** The entry tool (`DynamicSubAgentTool`, or the
  hand-coded regulated tools via `template_parameters()`) merges the
  properties into its OpenAI schema next to `message`, so the orchestrator
  Planner fills them in the same call that invokes the agent.
- **Seeding.** Validated values seed `state.variables` before the graph
  runs; the raw set is also exposed at `main_context.planner_args`.
  Validation is lenient — wrong type / enum violation / empty value →
  dropped with `[sub_agent_arg_dropped.v1]`, never an error.
- **Parse skip/narrow.** On the seeded Planner-entry pass only, the entry
  `parse_node` skips its LLM call when all its write targets are filled
  (`[parse_node_skipped.v1]`) or narrows `output_schema` to the missing
  fields (`[parse_node_narrowed.v1]`). Interrupt-resume passes always parse
  in full, so mid-flow corrections keep overwriting slots. Per-node opt-out:
  `always_run: true`.
- **Seed-once.** The entry tools apply seeding once per Planner tool_call
  (`variables._planner_args_call_id`); LangGraph interrupt replays re-run
  `execute()` from the top and must never re-apply stale args. A new
  Planner call over an abandoned (non-terminal) flow seeds fill-empty-only.
- **Slot safety.** `writes` may not target `_`-prefixed variables, any node
  `output_var`, or any interrupt `targets_slot` — validated against every
  channel variant's graph at upsert/import time, so confirmation gates stay
  structurally unreachable to the Planner. A data-collection interrupt can
  opt its slot in with `"planner_fillable": true` (a pre-filled slot then
  skips the interrupt entirely, which is the point).

Shared helpers: `app/tools/sub_agent_params.py`. Loader validation:
`template_loader.validate_parameters` / `protected_slots`.

## Per-agent context (knowledge blob)

`SubAgentTemplate.context` is a Markdown blob that travels with the
template. The compiler injects it as `_agent_context` into each node's
data dict; `llm_node` and `parse_node(mode=llm)` auto-prepend it to
their system prompt unless the node sets `data.include_context = false`.

Use it for domain facts that every LLM call in the sub-agent benefits
from — card comparison tables, eligibility rules, product specs. The
runtime treats it as opaque text. Authored via the **Context** tab in
the Agent Builder UI.

When to reach for this vs. an LLM-side `knowledge_search` tool:
- **Context tab** — small, static, every-turn-relevant knowledge.
- **`knowledge_search`** — large corpus, targeted lookups, paid per call.

See `extending_tools_and_agents.md` → "Per-agent context (knowledge
blob)" for the full authoring story.

## Runtime prompt substitution

All four template-style fields are resolved by the shared
`app/utils/templates.py:resolve_templates(value, state)`:

- `interrupt_node.prompt_template`
- `tool_call_node.params_template`
- `response_node.text_template` / `glass_template` / `widget.data_template`
- **`llm_node.system_prompt` / `parse_node(mode=llm).system_prompt`** (added in PR 2)

Resolution semantics:
- `{{some.path}}` (exact-match) → raw passthrough (preserves type)
- `"Hello {{name}}"` (embedded) → string substitution
- Lookup: `state.variables` first, then top-level state (`user_id`,
  `session_id`, `channel`)
- Missing reference → empty string in substitution mode, `None` in
  passthrough mode

The Agent Builder's Variables panel (next to the system_prompt
textarea) lists every upstream-writable slot plus state scalars as
click-to-insert buttons. Discovery is BFS over the edge graph from the
current node back to the entry node.

## State (`SubAgentState`)

Lives in `app/agents/state.py`. Shape:

- `messages` — `add_messages`-annotated; appended to as the inner graph
  runs LLM calls
- `variables` — slot scratchpad; written by `parse_node`, `tool_call_node`
  (via `output_var`), `interrupt_node` (via `targets_slot`)
- `channel` — pinned at procedure entry; predicates can branch on it
- `session_id`, `user_id` — flowed in from the outer state
- `main_context` — read-only view of the main orchestrator's
  enrichment context (so sub-agents have access to profile + memory
  facts when they need them)
- `_terminal` — set by `response_node` to mark the inner graph as done
- `_escape_kind` — set by the escape classifier to signal abort or
  topic-change

## Escape classifier — runtime guarantee

`app/agents/escape.py`. Every user reply that re-enters the inner graph
goes through the classifier BEFORE the parse_node sees it. Three
outcomes:

- `abort` — user wants out (e.g. "cancel", "nevermind") → graph routes
  to the escape-target `response_node` (the one with
  `data.is_escape_target = true`)
- `topic_change` — user changed the subject → same escape route
- `continue` — proceed with the parse as normal

Authors cannot disable this. The compiler injects a synthesized
priority-0 edge on every condition_node that checks
`has(variables._escape_kind)` and routes to the escape target. These
runtime edges show up dashed orange in the canvas.

## Channel pinning

The channel is pinned at procedure entry. If the resume request comes
in on a different channel than `supported_channels` allows, the runtime
returns `CHANNEL_UNAVAILABLE` with the template's
`unsupported_channel_message`.

Interrupt payload (surfaced via the SSE `interrupt` event from the
chat router):

```json
{
  "kind": "slot_prompt",
  "prompt": "What's the transfer amount?",
  "channel": "chat"
}
```

Resume: `POST /api/chat/sessions/:id/messages` with `type="resume"` and
`data = { "utterance": "...", "widget_instance_id": "..." }`.

## Locked principles

1. **Tools own business rules; sub-agents own experience.**
2. **Routing is deterministic.** Predicates, not LLMs, decide the next
   node. `llm_node` exists for content generation, not orchestration.
3. **Step-up auth is never a sub-agent concern** — the tool layer
   handles it.
4. **Escape classifier is a runtime guarantee** — not a node, not
   skippable, applied to every resume.
5. **Partial-slot persistence is OFF by default**; opt in via
   `suspend_resume_allowed` on the template.
6. **Regulated templates** (`is_regulated=true`) cannot use
   `return_mode=to_presenter` and cannot contain free-form `llm_node`
   (the loader rejects them at load time). They must use structured
   output schemas.
7. **`tool_call_node.post_write` must be a flat JSON-serializable
   dict.** Nested data belongs in the tool's return + `output_var`.
8. **Slot names may appear in `POLICY_BLOCK` reasons; slot values may
   not.** The runtime redacts.
9. **Log schema is versioned** — every event suffixed `.v1` /
   `.v2` etc. so observability queries don't break silently.
10. **Sub-agents cannot invoke other sub-agents** from within the inner
    graph. Cross-sub-agent flow goes via `return_mode=to_orchestrator`
    back through the Planner.

## Authoring

**Non-regulated agents** — author in the Agent Builder UI
(`/agents/builder`). Save & Deploy hits `POST /api/agents` →
`upsert_template()` writes a `source='user'` row → `_refresh_registry()`
rebuilds the sub-agent registry. No backend restart needed.

**Regulated agents** — edit the JSON file in `app/agents/templates/`,
PR + code review, then apply via the admin import endpoint after the
new image is deployed: `POST /api/agents/admin/import-file/{filename}`
or the helper `backend/scripts/import_seed.sh`. The new image's file
content is NOT auto-deployed on boot — bootstrap only runs against an
empty DB. See `deploy_runbook.md` → "Agent template deploys" for the
full deploy flow.

## File map

| Concern | File |
|---|---|
| Template → `StateGraph` compiler (runtime-injected escape + retry edges, `_agent_context` injection) | `app/agents/template_compiler.py` |
| Template validator + `LoadedTemplate` dataclass (`name`, `context`, regulated flags, etc.) | `app/agents/template_loader.py` |
| DB-backed template store (read / upsert / import) | `app/agents/template_store.py` |
| Bootstrap-from-files + agent registry init | `app/agents/templates/__init__.py`, `app/agents/__init__.py` |
| Node factories (parse / condition / interrupt / tool_call / llm / tool / response) | `app/agents/nodes/` |
| Predicate DSL compiler | `app/agents/predicates.py` |
| `{{var}}` substitution resolver | `app/utils/templates.py` |
| Per-thread driver runtime (interrupt resume, accumulated inner state) | `app/agents/runtime.py` |
| Escape classifier (`abort` / `topic_change` / `continue`) | `app/agents/escape.py` |
| Regex parsers + LLM structured-output helper | `app/agents/parsers/` |
| Seed JSON templates | `app/agents/templates/*.json` |
| Sub-agent template DB model | `app/models/sub_agent_template.py` |
| Tool wrappers that dispatch a sub-agent from the Planner | `app/tools/refund_tool.py`, `app/tools/transfer_tool.py`, `app/tools/dynamic_sub_agent_tool.py` |
| `/api/agents` CRUD + admin import + patterns | `app/routers/agents.py` |
| Frontend builder | `frontend/src/components/agents/AgentBuilder.jsx` |
| Frontend canvas + property panel | `frontend/src/components/agents/graph/AgentCanvas.jsx`, `NodePropertiesPanel.jsx`, `AddNodeMenu.jsx` |
| Frontend node component (one component, dispatched by type) | `frontend/src/components/agents/graph/nodes/SubAgentNode.jsx` |

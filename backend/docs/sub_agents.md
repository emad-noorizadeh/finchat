# Sub-agents — deterministic template-backed architecture

Every sub-agent is a **template** (JSON) that the ProcedureRuntime drives
deterministically. No LLM orchestrates the flow; slot extraction, corrections,
escape handling, and confirm gates are pure-Python building blocks. The
orchestrator LLM is ejected once a regulated template starts.

Related docs:
- `backend/docs/subagent_rollout.md` — flag-gated rollout + rollback runbook
- `backend/docs/subagent_followups.md` — deferred items with PR/phase tags
- `backend/docs/architecture.md` — outer orchestrator (Planner + Presenter)

## Why this shape

Widgets let chat sub-agents hide under a form; voice has no form. The old
LLM-driven Transfer sub-agent relied on conversational judgment at every
step (what to ask, how to parse, when to confirm, whether to execute).
That produces malformed tool payloads, skipped confirmations, and dead
loops — bad UX even when the tool itself is safe.

v2 splits responsibility:

| Layer | Role |
|---|---|
| Tool | Owns business rules, auth, limits, fraud — **unchanged**. |
| Template | Declares procedure shape: slots, confirm summary, tool to execute. |
| Sub-agent runtime | Drives templates deterministically. No LLM. |
| Orchestrator | Routes user intent to sub-agents and paraphrases their returns. |

## Graph-level view

```
User
  │ POST /api/chat/sessions/:id/messages
  ▼
Main orchestrator (Planner + Presenter)
  │ calls transfer_agent tool
  ▼
TransferAgentTool.execute
  │ loads domestic_transfer_v1 template
  ▼
ProcedureRuntime
  │ drives nodes via LangGraph interrupt() per user turn
  ▼
slot_collector → confirm_step → execute_tool
                                   │
                                   ▼
                               transfer_money service (business rules)
```

`interrupt()` inside the tool's execute pauses the outer graph. On resume,
the tool's execute re-runs from the top; each `interrupt()` call returns
its cached resume value from LangGraph's per-task resume list. The runtime
is deterministic so replay produces the same sequence of prompts.

## Template shape

File: `app/agents/templates/*.json`. One file per template. Loaded at module
import via `app/agents/templates/__init__.py` → validated via
`app/agents/template_loader.py`.

```json
{
  "name": "domestic_transfer_v1",
  "agent_name": "transfer_agent",
  "template_schema_version": 1,
  "is_regulated": true,
  "supported_channels": ["chat", "voice"],
  "suspend_resume_allowed": false,
  "locked_for_business_user_edit": true,
  "unsupported_channel_message": "Transfers aren't available on this channel.",
  "nodes": [
    { "id": "collect", "type": "slot_collector", "data": { ... } },
    { "id": "confirm", "type": "confirm_step",  "data": { ... } },
    { "id": "execute", "type": "execute_tool",  "data": { "tool": "transfer_money" } }
  ],
  "edges": [
    { "source": "collect", "target": "confirm" },
    { "source": "confirm", "target": "execute" }
  ]
}
```

## Node types (v2)

Four types, registered in `app/agents/nodes/`:

### `slot_collector`
Collects N typed slots through a deterministic loop. Each slot has:
- `name`, `type` (one of `money`, `account_ref`, `yes_no`, `enum`, `date`)
- `prompt` and optional `correction_prompt` (correction-aware re-ask)
- `repeat_back` (boolean; forced true on `money`)
- `min_confidence` (0.0–1.0)
- Optional `validators: ["positive", "within_daily_limit"]`
- Optional `cross_slot_validators: ["different_from:from_account"]`
- For `account_ref`: `options_source` + `options_depends_on`

### `confirm_step`
Renders a summary (chat: `confirmation_request` widget; voice: glass),
captures yes/no. Four edge outcomes:
- `on_confirm` — user confirmed
- `on_decline` — user declined
- `on_modify` — user tried to correct a slot (routes through correction cascade)
- `on_cancel` — user aborted

### `disambiguation_step`
Pick-one-from-a-resolved-list. Different from slot_collector — no typed
extraction from free text, user picks by index or label.

### `execute_tool`
Terminal. Invokes a real tool with the collected slots. The tool is the
final authority on business rules; its return shapes the `SubAgentResult`.

## Slot type library

`app/agents/slot_types/`. Engineer-owned. Types carry their own policy:

| Type | Tiers | Policy |
|---|---|---|
| `money` | regex | `repeat_back=True` forced; `positive` validator; min_conf 0.85 |
| `account_ref` | last-4 regex + type-hint | options-aware; no repeat_back |
| `yes_no` | regex | binary; no repeat_back |
| `enum` | label + index match | options-aware |
| `date` | regex (ISO, MM/DD/YYYY) | `repeat_back=True` |

Adding a new type is a code change — reviewed, tested, deployed.

## Escape classifier — runtime guarantee

Every interrupt resume goes through `app/agents/escape.py` BEFORE slot
extraction or confirm parsing. Three outcomes: `abort`, `topic_change`,
`continue`. Phase 1 is keyword-only; Phase 2 adds a narrow LLM classifier.
Authors cannot skip or disable this; the runtime enforces it on every node.

## Corrections model

`app/agents/corrections.py`. Detects mid-flow value updates and cascades.
Detection rules (first match wins):
1. Keyword trigger: "actually", "wait", "change", "instead", "make it", …
2. Unique-parse: utterance parses cleanly as a type held by exactly one
   *filled* slot, AND that slot is NOT the current slot.

Cascade wipes the corrected slot, anything with `options_depends_on` or
`cross_slot_validators` referencing it, and (by default) everything
declared after it. Pre-extracted value is threaded through so the same
utterance isn't re-parsed twice.

## Structured return

`app/agents/result.py` — `SubAgentResult(status, reason, collected_state,
widget, glass, user_facing_message, audit_trail)`. Mapped in
`TransferAgentTool._map_to_tool_result` to a `ToolResult` for the parent
orchestrator. Tool failures declare `ToolResult.error_category`; runtime
maps category → reason.

## Channel handling

Channel is pinned at procedure entry. If the resume channel differs and
the template's `supported_channels` excludes it → `CHANNEL_UNAVAILABLE`
with the template's `unsupported_channel_message`.

Interrupt payload (exposed via SSE `interrupt` event):
```json
{ "kind": "slot_prompt", "prompt": "<text>", "channel": "chat" | "voice" }
```

Resume: `POST /api/chat/sessions/:id/messages` with `type="resume"` and
`data={"utterance":"..."}`.

## Locked principles (cumulative through v5 plan)

1. Tools own business rules; sub-agents own experience.
2. Step-up authentication is NEVER a sub-agent concern.
3. Escape classifier is a runtime guarantee — not a node, not skippable.
4. Slot types carry their own policy; instance config cannot override type policy.
5. Partial-slot persistence OFF by default; opt-in via `suspend_resume_allowed`.
6. Orchestrator LLM is ejected during regulated templates.
7. Corrections are first-class, not retry.
8. Tool error categories required on failure; missing defaults to `SYSTEM`.
9. Regulated templates cannot contain free-form LLM calls.
10. Slot names may be exposed on `POLICY_BLOCK`; slot values may not.
11. Log schema versioned from day one (`.v1` suffix on every event).
12. Post-confirmation, procedure committed to tool return; procedure ends there.
13. Tool error strings are internal; `user_facing_message` is orchestrator-facing.
14. Sub-agents cannot invoke other sub-agents (Phase 1).
15. Corrections detector runs on every utterance with unique-parse brake.

## Authoring

**Phase 1: code.** Edit `app/agents/templates/*.json`, restart.

**Phase 3: UI.** Template Config editor for business users (prompts, retry
counts, limits). Template structural edits stay engineer-only.

`/api/agents` returns templates as read-only metadata for the existing
AgentBuilder UI. Write endpoints (POST/PUT/DELETE/deploy/disable) return
HTTP 501 in Phase 1 with a pointer to Phase 3 template authoring.

## What was deleted in v2 cutover

| Removed | Replacement |
|---|---|
| `TransferChatAgent`, `TransferVoiceAgent` (hand-coded classes) | `domestic_transfer_v1.json` template |
| `DynamicSubAgent` (DB-loaded legacy-graph agents) | File-based templates via `app/agents/templates/__init__.py` |
| `BaseSubAgent` class + `_build_default_graph` + `_make_*_node` handlers | `ProcedureRuntime` + typed node handlers in `app/agents/nodes/` |
| `default_graph_definition()` (legacy 3-node JSON factory) | Default v2 graph in `AgentBuilder.jsx:buildDefaultGraph` |
| `request_confirmation` tool + `interrupt()` inside `_tool_node` | `confirm_step` node |
| Legacy node types: `llm`, `tool`, `response`, `condition`, `custom_tool`, `extra_llm` | `slot_collector`, `confirm_step`, `disambiguation_step`, `execute_tool` |
| Frontend node components for legacy types | `SlotCollectorNode`, `ConfirmStepNode`, `DisambiguationStepNode`, `ExecuteToolNode` |
| `AgentDefinition` seeding + DB loading flow | Templates live in files; no DB bootstrap |
| `/api/agents` CRUD write endpoints | Phase 1: 501. Phase 3: template authoring UI. |

## File map

| Concern | File |
|---|---|
| Procedure runtime (deterministic driver) | `app/agents/procedure_runtime.py` |
| Template JSON schema + validator | `app/agents/template_loader.py` |
| Template discovery (file-based) | `app/agents/templates/__init__.py` |
| Domestic Transfer v1 template | `app/agents/templates/domestic_transfer_v1.json` |
| Node registry + handlers | `app/agents/nodes/{registry,slot_collector,confirm_step,disambiguation_step,execute_tool}.py` |
| Slot type library | `app/agents/slot_types/{base,yes_no,money,account_ref,enum,date_type}.py` |
| Options resolvers | `app/agents/resolvers/__init__.py` |
| Escape classifier | `app/agents/escape.py` |
| Corrections detector + cascade | `app/agents/corrections.py` |
| Structured return types | `app/agents/result.py` |
| Sub-agent registry | `app/agents/__init__.py` |
| TransferAgentTool (tool surface) | `app/tools/transfer_tool.py` |
| Transfer service (tool-layer business rules) | `app/tools/transfer_actions.py`, `app/services/transfer_service.py` |
| Templates API (read-only) | `app/routers/agents.py` |
| Frontend builder | `frontend/src/components/agents/AgentBuilder.jsx` |
| Frontend canvas + property panel | `frontend/src/components/agents/graph/{AgentCanvas,NodePropertiesPanel,AddNodeMenu}.jsx` |
| Frontend node components | `frontend/src/components/agents/graph/nodes/{SlotCollectorNode,ConfirmStepNode,DisambiguationStepNode,ExecuteToolNode}.jsx` |

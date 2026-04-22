# Widget architecture — design history & rationale

> **This doc is historical.** The current contract and authoring guide live in [`widgets.md`](./widgets.md). Read that first.

The widget path has been through two phases:

- **v0–v7: LLM Presenter.** A language model bound to a list of render tools picked one per widget turn. See the `Planner / Presenter split` framing in earlier revisions of `architecture.md`.
- **v8 (current): Deterministic Presenter.** A pure-Python rules engine (`select_render`) replaces the LLM. Render tools collapsed into builder functions referenced directly by catalog entries.

This doc captures *why* the reframe happened and what changed, so future maintainers don't re-derive the decision.

## Why the LLM Presenter was removed

Three reasons:

1. **The decision was already expressible as rules.** Every branch of the Presenter's system prompt (designed composite, single slot, generic composite, text fallback) mapped 1:1 to a catalog lookup. The LLM was pattern-matching over catalog descriptions — hard-coding the match is strictly better when the patterns are enumerable.
2. **Cost and latency.** Every widget turn paid for one LLM call plus ~1 s round-trip — for a decision that was a dict lookup.
3. **Non-determinism under review.** Seven rounds of review validated the rules on paper; shipping an LLM path would have required shadow-mode validation against real traffic to earn the same confidence.

The plan's escape hatch (adding an LLM back for genuine ambiguity as a "rule 0" or "rule 1.5") remains open if production logs ever show rule-4 fallbacks clustering on cases a classifier could fix. Today the rules cover every observed case.

## What changed (v7 → v8)

| Area | v7 (LLM Presenter) | v8 (deterministic) |
|---|---|---|
| Presenter | `bind_tools([render_tools])` + system prompt + LLM | `select_render(state)` pure function |
| Render tools | `app/tools/render_tools.py` (339 lines) — `BaseTool` subclasses | **Deleted.** Their two responsibilities (slot unwrap + builder call) live in the Presenter. |
| `is_render` flag on `BaseTool` | Filtered render tools from Planner's `bind_tools`, included them for the Presenter's | **Deleted.** No tool is "render" anymore. |
| `fast_path_synth` node | Synthesized an `AIMessage(tool_calls=[render_X(...)])` for single-slot turns | **Deleted.** Rules engine handles all cases uniformly. |
| `after_presenter` | Routed Presenter's tool-call AIMessage to `tool_execute` | **Deleted.** Presenter is terminal (edge to END). |
| Catalog | `render_tool: str` (tool name) | `render_fn: callable` + `slot_arg_map` + `sample_build_args` + `composite_priority` |
| Render error | `tool_execute` caught render tool exceptions, emitted text_card apology | `presenter()` wraps builder call in try/except, same text_card apology |
| LLM calls per widget turn | 2 (Planner + Presenter) | 1 (Planner) |

## The four rules

Canonical reference lives in `app/agent/presenter.py:select_render`. Summary:

1. **Designed composite** — populated slots exactly match some `slot_combination`. Tiebreak by declaration order.
2. **Single slot** — exactly one populated slot with `default_data_var`.
3. **Generic composite** — 2+ composable mapped slots → `render_generic_composite`; up to 3 sections ordered by `composite_priority` then population order.
4. **Text-card fallback** — anything else; content from Planner prose or concatenated `widget_to_llm` summaries.

See [`widgets.md`](./widgets.md) for full semantics, invariants, and authoring guide.

## Sub-agent widget path — unchanged

Sub-agents have their own widget emission path via `ToolResult(widget=...)` returned from the sub-agent's response or custom_tool nodes. `tool_execute`'s widget-emission code (persist + dispatch + terminate) handles these; the Presenter doesn't participate.

The rule holds: **sub-agent widgets go through `tool_execute`; main-orchestrator widgets go through `presenter`.** Both paths call `WidgetService.create_instance` and `dispatch_custom_event("widget", ...)`.

## When to add an LLM back

Not yet. Watch production logs for:

- **`[presenter_designed_composite_missed]`** high frequency → over-fetching is costing designed-composite quality. Consider a subset-match "rule 1.5" or a larger designed composite.
- **Rule-4 fallbacks** clustering on ambiguous intent queries ("compare my accounts" when only one slot is populated) → add an intent signal (regex on user query, embedding similarity, or a tiny Haiku classification call) as rule 2.5.

Neither of these requires reinstating a full LLM Presenter. They're narrow classifiers that break ties; the rules engine keeps driving the common cases.

## Invariants (still hold)

1. **Data tools never emit widgets.** They write `state.variables[output_var]`. Widgets come from the Presenter or from sub-agents.
2. **Builders are pure.** `(kwargs) → widget dict`. No catalog lookups, no state access. Composite section rendering is the frontend's job.
3. **Catalog is the single source of truth.** Adding a widget = new builder + catalog entry. No registration elsewhere.
4. **Presenter is terminal.** Emits widget, sets `response_terminated=True`, edge to END.
5. **The Planner never calls builders directly.** Only `present_widget()` hands off to the Presenter.
6. **One widget per turn** via the main-orchestrator path. Sub-agent widgets may terminate the turn independently via `tool_execute`.
7. **Sub-agent graphs and main orchestrator use different mechanisms** but share the same catalog — sub-agents define `response_format="widget"` in response nodes; the main orchestrator goes through the Presenter rules engine.

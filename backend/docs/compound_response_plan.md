# Compound responses — prose + widget in one turn

## Problem

The Planner's widget hand-off terminates the turn. If a user asks a question
that needs BOTH data lookup AND synthesis against knowledge (regulation,
policy, account terms), today's graph gives them only the widget — no
explanation.

Concrete case that motivated this plan (turn `fe8bdb29`, 2026-04-21):

> User: "can you tell me why I got fee in my savings"

Logs:
```
[tool_execute.v1] tools=['get_transactions_data', 'present_widget']
[router_decision] branch=presenter
[presenter_choice] rule=single_slot widget=transaction_list
[llm_call.v1] iteration=1 ... tool_calls=['get_transactions_data','present_widget'] content_len=0
```

What happened:
- Planner read "fee in savings" as a transaction lookup.
- One iteration. No `knowledge_search` call. No narration.
- The widget renders one row (Savings Monthly Fee, $5.00, 03/31).
- The "why" is unanswered.

The user sees data but no explanation.

## Root cause

Two modes are conflated in a single turn:

1. **Data gathering** — tools that write to `state.variables` for later use.
2. **Presentation commitment** — `present_widget()` telling the Presenter to
   build a widget and END the graph.

Because `present_widget()` is terminal, the Planner can never call it *and*
reason over the tool results it just requested. It has to commit to "widget"
before seeing any data. And our prompt explicitly forbids narration alongside
the widget:

> *"When you call `present_widget()`, do NOT write text content in the same
>  message. The widget is self-describing."*

So even when the Planner has context for an explanation, it can't deliver
both a widget and prose. Today's flow, in graph terms:

```
Planner → tool_execute (data+present_widget) → Presenter → widget → END
                                                              (content_len=0)
```

## Non-goals

- Preserve the fast-path for simple "show me X" queries. One LLM call.
- Do NOT add a narrator LLM after every widget turn — that doubles cost on
  the common case where narration adds nothing.
- Sub-agent widget paths (transfer/refund) are unchanged. Those are
  terminal by design and their `_tool_result` envelope already carries a
  to_llm summary for the parent.

## Design

Three shapes were considered; the one we're building is a hybrid.

### Shape A — Narrator-after-widget node (rejected)

Graph: `Planner → tools → Presenter → Narrator → END`.
Cost: +1 LLM call on every widget turn. Not worth it for "show my profile"
style queries where the widget IS the complete answer.

### Shape B — Compound final AIMessage (prose + widget) (adopted, partial)

Allow the Planner's final AIMessage to carry both `content` (prose) AND
`tool_calls=[present_widget()]`. The router streams the prose, THEN hands
to the Presenter for the widget. Two renderable surfaces, one turn.

Required only when the Planner already has context to narrate — which means
it must have seen tool results first. That's Shape C.

### Shape C — Two-phase loop for compound queries (adopted)

Turn 1: Planner calls data tools + `knowledge_search` (no `present_widget`).
Graph loops back to the Planner.
Turn 2: Planner has tool results in history → synthesizes prose. May also
emit `present_widget()` for a scannable view. Presenter renders widget
after prose streams.

Cost: 2 LLM calls instead of 1 — paid only when the Planner opts into the
two-phase path.

### Adopted: Shape C + Shape B together

- Simple "show me X" stays one-hop fast-path (unchanged today).
- Compound / "why" queries split: gather in turn 1, synthesize in turn 2.
- Turn 2's AIMessage is compound (prose + `present_widget()`) — Shape B
  wired into the two-phase path.

The Planner decides which path by intent. No new graph node; the existing
loop already supports multi-turn. The change is:
- Prompt teaches the Planner when to go two-phase.
- Router preserves `content` when Planner emits compound AIMessage.
- SSE streams prose first, widget second, consumer already renders both.

## Phase 0 — prerequisites (ship blockers, not "verify later")

These must pass BEFORE the prompt change goes out. They derisk assumptions
the design depends on.

### P0.1 — Frontend ordering is verified, not assumed

The entire UX rests on "prose bubble above widget card" for the main
orchestrator's compound response. SSE arrival order is not the same as
React render order — batching, key collisions, widget remounts,
scroll-anchor logic can all reorder. Don't ship the prompt change until:

- A synthesized test message with `content` followed by a `widget` event
  from the main orchestrator renders the content above the widget.
- Coverage: (a) first message in a session, (b) mid-session after prior
  turns.
- If the order is wrong, fix the chat consumer first. File paths:
  `frontend/src/components/chat/ChatMessage.jsx`,
  `frontend/src/pages/ChatPage.jsx` (message-list reducer).

**Out of scope for P0.1:** sub-agent resume (transfer/refund interrupt →
Command(resume) → widget). Sub-agent widgets are terminal-by-design;
compound prose-above-widget is not a pattern there today, and its
followup narration would ship under the sub-agent compound followup (see
§Followups). Expected DOM order for sub-agent resume is unchanged:
widget-only, no prose above.

### P0.2 — `present_widget()` mechanics documented and asserted

Clarify the mechanism on the record: `present_widget()` is a tool registered
in `app/tools/handoff.py` with a no-op `execute()`. It exists so the LLM can
emit a tool_call with a legitimate name and schema. The router in
`app/agent/nodes.py:582` inspects the Planner's AIMessage and routes to the
Presenter when any tool_call has `name=="present_widget"`. `tool_execute`
still runs the tool — the no-op returns an empty `ToolResult(to_llm="")` —
so LangGraph's tool_call/tool_result pairing stays valid. The Presenter
branch is taken regardless of that return.

This is load-bearing for the router decision, so add a unit test that
asserts:
- `present_widget.execute()` returns an empty ToolResult (no widget, no
  to_llm content, no terminal flag).
- Router's `post_tool_router` returns `"presenter"` when Planner AIMessage
  has `present_widget` in tool_calls, regardless of turn history.

## Files to change

### Backend

| File | Change |
|---|---|
| `app/services/enrichment.py` | Rewrite the "Widget vs prose" section (spec below). Keep a HARD rule scoped to the fast-path: *"If you are not on the two-phase path, do not write content alongside present_widget()."* Two-phase is the only path where narration + widget are both allowed. |
| `app/agent/nodes.py` | In the Presenter-branch path, ensure the Planner's `AIMessage.content` reaches the SSE stream as `ai_chunk` events before the Presenter emits the `widget` event. Log `with_narration=true` on `[router_decision] branch=presenter` when `content` is non-empty. |
| `app/agent/nodes.py` | Add a two-phase hop guard: if `state["iteration_count"] > 2` AND `response_terminated` is False AND the Planner keeps emitting tool_calls that don't include `present_widget()`, force-terminate via a text_card fallback ("I'm having trouble synthesizing a complete answer from the available data"). Existing `max_agent_iterations=15` is a hard cap; this is a softer two-phase-specific cap at 2. **Log `[hop_guard_triggered]` with the Planner's intended `tool_calls` on trigger** — separate signal from "text_card fallback due to other reasons." This lets observability distinguish "Planner legitimately wanted turn 3" (e.g., KB → balance lookup → narrate) from "Planner stalled in a tool loop." If eval / production shows recurring legitimate third-hop patterns, relax the cap to 3 as a followup (we can only decide from the log signal, not from absent evidence). |
| `app/agent/nodes.py` | Add error handling for turn-1 partial results. If any turn-1 tool raises and tool_execute catches it into an error `ToolMessage`, the Planner loops to turn 2 with partial context. Prompt tells the Planner to narrate what it has and flag what it doesn't. No retry at the graph level — the Planner's turn-2 LLM call IS the retry. **Current behavior of `tool_execute` vs. raising tools is an assumption that MUST be verified (not asserted) — see unit test in the Verification section. If `tool_execute` re-raises on some exception classes, those must be caught and converted to error ToolMessages before this plan ships, otherwise the graph fails instead of looping back.** |
| `app/routers/chat.py` | Verify (with an integration test, not just inspection) that `ai_chunk` events stream before the `widget` event. SSE producer is `_event_generator`. |
| `app/agent/nodes.py` (`emit_turn_summary`) | Replace proposed boolean `compound_response` with three-valued `response_shape: "prose_plus_widget" \| "widget_only" \| "prose_only"` so dashboards can distinguish all three cases (today we also want to observe widget-only vs prose-only rates as context for the compound rate). |
| `app/services/llm_service.py` | Verify system-prompt length exceeds OpenAI's prompt-cache threshold (≥1024 tokens on gpt-5, automatic cache). If not, add a docstring note — we don't want to pay 2× LLM cost on compound turns without cache benefit. See "Phase 1 — cost mitigation" below. |
| `app/config.py` + prompt file | Add a `planner_prompt_revision: str = "v2026-04-22"` setting gated into `enrichment.py`. Lets us ship the old prompt as a rollback by flipping the env var — no redeploy. |

### Frontend

| File | Change |
|---|---|
| `frontend/src/components/chat/ChatMessage.jsx` (and reducer) | Gated by P0.1 — if ordering is wrong, fix it. If already correct, add a Playwright test that asserts the DOM order (prose bubble → widget card) for a compound message. |

### No change

- Presenter (`app/agent/presenter.py`) — rule engine is orthogonal to whether
  the AIMessage carried prose.
- Data tools — already non-terminal.
- Sub-agent widget path — terminal by design, unchanged. Transfer/refund
  rejection narration ("here's why this transfer failed") wants the same
  compound pattern eventually; tracked as a followup (see §Followups).

## Prompt change (the load-bearing part)

Rewrite `app/services/enrichment.py` "Widget vs prose" section:

```
## Widget vs prose

Three turn shapes, pick deliberately. The distinguishing feature is
whether you emit present_widget() and, if so, in which turn.

FAST PATH (one turn, NO narration) — "show me / list / what's my":
  Call the data tool + present_widget() in the SAME message.
  Example: "show my transactions" → [get_transactions_data(view="recent"),
  present_widget()]. Do NOT write content — the widget is self-describing.
  HARD RULE: on the fast path, content must be empty. If you catch yourself
  typing a sentence, you picked the wrong path — go two-phase.

TWO-PHASE (gather → synthesize) — needs BOTH data AND policy explanation:
  Trigger when the user asks WHY something happened AND answering requires
  both the specific instance (data) AND the rule behind it (KB policy).
  Examples:
  - "why did I get a fee" — data (which fee?) + policy (fee rules).
  - "why was my transfer denied" — data (the attempt) + policy (limits).
  - "why is there a hold on my deposit" — data (which deposit, amount,
    release date) + policy (hold rules). This is the tiebreaker case: when
    BOTH data AND policy are needed, go two-phase. Prefer two-phase over
    trying to answer from data alone.
  - "how does cash back tier work" — pure policy lookup, still two-phase
    (knowledge_search in turn 1; narrate in turn 2, no widget).

  Turn 1: call the data tool(s) AND knowledge_search. Do NOT call
  present_widget(). The graph loops back.
  Turn 2: you now see the data + KB citations. Narrate the explanation.
  You MAY also call present_widget() in this same message — prose streams
  first, then the widget renders below it. The widget is optional in
  two-phase; skip it if the answer is a single fact.

  Turn-2 edge cases:
  - If turn-1 knowledge_search returned no useful match, synthesize
    best-effort from data alone and say what you could and could not find.
    Do not invent policy text. Example phrasing: "I can see the fee but
    I don't have the specific policy details for that fee type."
  - If turn-1 data tool errored, narrate what you have and flag what you
    don't. The error message will be in your context.
  - Do NOT loop a third time to re-query. You get two phases; turn 2 is
    the answer.

NO_WIDGET (narrate only, zero or more tools in a single turn):
  Every case that doesn't call present_widget() at all. Covers:
  - Data-reasoning "why" ("why is my balance so high") — data tool +
    narrate. No KB needed.
  - Conversational single-fact ("did I pay rent this month") — data tool
    + narrate, or zero tools.
  - General-knowledge explanations ("what's a good savings rate") —
    knowledge_search + narrate. Same call shape as two-phase turn 1 but
    never followed by a present_widget() turn — the answer is prose.
  Naming note: this shape is called NO_WIDGET because the routing
  signature is "no present_widget() in any turn." Surface content varies
  (may include tool calls and prose); the routing consequence is what
  matters.

Choosing path by intent:
- Needs data AND policy → TWO-PHASE.
- Needs pure policy explanation (no account data) → TWO-PHASE
  (knowledge_search in turn 1; narrate in turn 2, no widget).
- Needs data alone, answer is a narration ("why is X so high") → NO_WIDGET.
- Needs data alone, answer is a scannable list → FAST PATH.
- Pure conversational / single fact → NO_WIDGET.

Hard rules:
- In voice mode, always respond in prose. Do NOT call present_widget().
- You do NOT call render tools directly — present_widget() is the only
  widget-emission path.
- Knowledge-derived answers still forbid inventing Sources; SSE appends
  the citation block.
- Fast path forbids narration content. Two-phase turn-2 allows both.
```

## Concrete turn traces after the change

**Simple query (unchanged):**

```
User: "show my transactions"
Turn 1: Planner → [get_transactions_data(view="recent"), present_widget()]
        tool_execute → Presenter → widget → END
llm_calls=1, response_shape=widget_only
```

**Why query (new path):**

```
User: "why did I get a fee on my savings?"
Turn 1: Planner → [get_transactions_data(query="fee", account="savings"),
                   knowledge_search("savings monthly fee policy")]
        tool_execute → loop back to Planner
Turn 2: Planner, now seeing tool results → emits:
        content = "You were charged the $5 Savings Monthly Fee on 03/31.
                   This fee applies when the average daily balance falls
                   below $500 during the statement period…"
        tool_calls = [present_widget()]
        tool_execute (no-op for present_widget) → Presenter → widget → END
SSE stream: ai_chunk (prose) … ai_chunk … widget event.
llm_calls=2, response_shape=prose_plus_widget
```

## Verification

1. **P0 gates (must pass before prompt change ships):**
   - P0.1 Frontend: Playwright test confirms prose-bubble-above-widget DOM
     order for a compound message. Covers first-message and mid-session
     cases (sub-agent interrupt-resume is explicitly out of scope per
     P0.1).
   - P0.2 Backend: `present_widget()` mechanics unit tests (router routes
     to Presenter regardless of tool return; tool returns empty ToolResult).
2. Unit: router preserves `content` on `branch=presenter` when Planner
   AIMessage has both content and `present_widget()`.
3. Unit: two-phase hop guard — synthesized state with `iteration_count=3`
   and no `present_widget()` in latest tool_calls force-terminates with a
   text_card fallback AND emits `[hop_guard_triggered]` log with the
   Planner's intended tool_calls.
4. **Unit: tool error is caught into an error `ToolMessage` (not re-raised).**
   Register a test tool whose `execute()` raises (on both `ValueError` and
   a generic `Exception`). Invoke `tool_execute` with a Planner AIMessage
   calling it. Assert: (a) the graph does NOT raise up to the driver, (b)
   the next node sees a `ToolMessage` with non-empty `content` describing
   the error, (c) the graph loops back to the Planner node. This verifies
   the plan's error-path assumption instead of asserting it.
5. Eval rows: turn-1 error → turn-2 narration, and empty-KB → turn-2
   narration without fabrication. Both are LLM-driven behaviors, not unit
   tests — see Eval section, rows labeled "Empty-KB fallback" and
   "Turn-1 error" respectively.
6. **Integration: content-before-tool-calls streaming order (Phase 1
   verification, added due to streaming-assumption risk).** The prompt
   says narration streams before the widget, which assumes the Planner
   emits content deltas before the `present_widget()` tool_call delta in
   a single AIMessage. OpenAI's streaming protocol does NOT guarantee
   this — most models default content-first, but it's not contractual.
   If tool_call deltas arrive first, SSE `ai_chunk` events arrive AFTER
   the Presenter has already dispatched the `widget` event, and P0.1's
   frontend ordering fix can't save us because upstream order itself is
   wrong.
   Test: capture the raw OpenAI streaming response for a realistic
   two-phase turn 2 (e.g., "why did I get a fee?"). Assert that the
   first non-empty streaming delta is a content delta, not a tool_call
   delta. Run 10 trials to defend against sampling variance.
   Remediation options if the test fails:
   (a) Add an explicit prompt instruction: "Emit your narration before
       calling present_widget()."
   (b) Switch turn-2 to OpenAI's structured-output mode with an
       ordered-field schema that forces content-first serialization.
   (c) Buffer the compound AIMessage in the router and re-dispatch
       content before widget (last resort — defeats streaming).
   Pick (a) first; it's cheap. Escalate to (b) only if (a) is unreliable.
6. Integration via `/chat` SSE:
   - "show my transactions" → content_len=0, one `widget` event,
     `response_shape=widget_only` in turn_summary.
   - "why did I get a fee on savings?" → `ai_chunk` events with prose, one
     `widget` event after. `response_shape=prose_plus_widget`. `llm_calls=2`.
   - "why is my balance so high?" → data tool called, NO knowledge_search,
     NO widget. `response_shape=no_widget`. `llm_calls=1` (or 2 if
     tool-then-narrate).
   - "why is there a hold on my deposit?" — boundary case requiring both
     data and policy → two-phase, `response_shape=prose_plus_widget` or
     `no_widget` depending on whether Planner emits the widget in turn 2.
     Empty-KB subcase: if policy lookup returns nothing, Planner still
     narrates what it has from data and flags the gap; does NOT invent
     policy text.
7. Voice channel: "why" query produces prose only (widget channel-suppressed),
   two-phase path still produces the same prose answer.
8. Rollback: flip `planner_prompt_revision` env var back to prior revision
   string, turn shapes revert to today's behavior without redeploy.

## Observability

Add to the existing `[turn_summary.v1]` log line:
- `response_shape=prose_plus_widget|widget_only|no_widget` (three-valued,
  replaces the earlier draft's `compound_response` boolean — lets us see
  all three rates side-by-side, which is what dashboards actually need).
  **Naming:** `no_widget` instead of `prose_only` because a NO_WIDGET turn
  may still call tools (knowledge_search + narrate, or data tool +
  narrate); the differentiator is the absence of `present_widget()`.
- `planner_prompt_revision=<string>` — so rollback analysis can compare
  distributions across revisions.
- `turn2_tool_delta=<comma-separated>` — tools turn 2 called that turn 1
  did NOT call. Empty when turn 2 just narrated.
- `turn2_tool_repeat=true|false` — true when turn 2 called any tool that
  turn 1 already called (intersection non-empty). Flags the "wasted
  loop" case directly instead of inferring it from the delta field.
  Semantics: `delta` = set-difference (unique to turn 2);
  `repeat` = intersection non-empty. The two fields cover both
  diagnostic questions cleanly — "did turn 2 advance?" (delta) and
  "did turn 2 redo work?" (repeat).
- **Both `turn2_tool_delta` and `turn2_tool_repeat` are ABSENT (not
  empty-string, not false) on single-turn shapes** (fast_path,
  no_widget with a single turn). Dashboard queries should filter on
  field presence, not value. Absence is a positive signal that the
  turn didn't have a phase 2 at all.
- `hop_guard_triggered=true|false` — distinct from text_card fallbacks
  emitted for other reasons (empty state, render errors). Only true when
  the 2-phase cap force-terminates an otherwise-looping Planner. Log
  alongside:
  - The intended third-hop tool_calls.
  - The turn-2 `content_len` at trigger time. `content_len > 0` means
    "the Planner was narrating AND also wanted more data" (legitimate
    intent, candidate for relaxing the cap). `content_len == 0` means
    "pure tool-loop, no narration" (stall pattern). These remediate
    differently — one argues for a 3-hop cap, the other for prompt
    tightening.
- (already present) `iterations`, `llm_calls`, `tools`.

### Soft-launch before thresholds

Phase 2's one-week observation window is a **soft launch, not dual-running
shadow mode**. The new prompt revision is enabled for 100% of traffic, but:
- No automated alerts tied to the new metrics.
- Dashboards populated and reviewed daily for anomalies (response_shape
  distribution, hop_guard_triggered rate, P95 latency delta).
- Rollback is one `planner_prompt_revision` env-flag flip away.

After the week, set alert thresholds from observed distribution + a
defensible budget (e.g., "P95 latency must not regress more than 500ms"
or "hop_guard_triggered rate must stay under X% of turns"). The earlier
draft's 15% number was unfounded — removed. Tune thresholds on real data.

If we later want true dual-running (e.g., A/B on prompt revisions), add a
traffic-split mechanism on `planner_prompt_revision` — that's a separate,
larger change, not part of this plan.

## Eval

Planner-routing eval extends the widget-architecture plan's harness.

### Sample size and distribution

- **100–150 labeled queries** (up from the earlier 30). 4 intent classes →
  ~25–35 per class, which gives defensible error bars at a 90%-accuracy
  target. Labels drawn from production-representative distribution: if
  production is 70% "show me" queries, the eval set is weighted 70%
  toward fast-path.
- Bootstrap source: replay last month of production chat queries from the
  `messages` table. Tag each with the expected turn shape.

### Label dimensions

| intent class | expected shape | triggers `knowledge_search`? |
|---|---|---|
| "show X" / "list X" / "what's my X" | fast-path: data + present_widget, no prose | no |
| Policy/fee/term "why" ("why was I charged", "what's the minimum balance rule") | two-phase: turn 1 data + knowledge_search, turn 2 prose + optional widget | yes |
| Boundary: data + policy "why" ("why is there a hold on my deposit") | two-phase: turn 1 data + knowledge_search, turn 2 prose + optional widget | yes |
| Data-reasoning "why" ("why is my balance high", "why did I spend so much") | NO_WIDGET: data tool + narrate | no |
| Yes/no / single fact ("did I pay rent") | NO_WIDGET: zero or one tool, narrate | no |
| General knowledge ("what's a good savings rate") | NO_WIDGET: knowledge_search + narrate | yes |
| Empty-KB fallback (synthetic: policy "why" where KB is stubbed empty) | two-phase turn 2 narrates without inventing policy | yes (returns empty) |
| Turn-1 error (synthetic: data tool raises) | turn 2 narrates what it has, flags the gap, does NOT retry | (depends on query) |

### Targets

- Overall classification accuracy ≥90% before prompt goes to production.
- **Empty-KB fallback: 100% of rows must NOT invent policy text.** Two-layer
  check because LLM judges are known to miss subtle fabrications:
  - **Layer 1 — LLM judge.** Default judge: `gpt-5`. Fallbacks (in order,
    only if the default is unavailable): `claude-sonnet-4-5`, then
    `gpt-5-mini`. The default is pinned to preserve reproducibility of
    the 100% target across runs; fallbacks exist only for outage cases.
    Judge prompt: "Given the KB passages retrieved (shown below) and the
    assistant's response, does the response claim any policy details not
    present in the passages? Answer YES/NO and quote the offending
    sentence if YES." Runs on 100% of empty-KB rows.
  - **Layer 2 — human spot-check on 10% of rows the judge marked clean.**
    Random sample. A single reviewer (product or engineering) validates
    the judge's "no fabrication" verdict. Target: 0 missed fabrications
    across the sample. Escalation ladder:
    1. Spot-check at 10% finds 0 misses → ship.
    2. Spot-check at 10% finds ≥1 miss → expand sample to 25%, tune the
       judge prompt, re-run Layer 1 on all rows. Any miss at 25% is a
       ship blocker.
    3. Judge tuning cannot achieve 0 misses at 25% → escalate to the
       two-independent-judges pattern (default + fallback-1 on all rows;
       any disagreement goes to human review).
    Rationale: a 10% clean sample can return zero misses on an unlucky
    draw even if the judge is actually leaky. The 25% expansion reduces
    that false-negative probability substantially without committing to
    dual-judging upfront.
- Fast-path narration discipline: 0% of fast-path rows emit content
  (hard rule in prompt; regression check).

### Re-run cadence

Re-run the full eval on every prompt-revision bump. Gate the revision
cutover on targets being met.

## Tradeoffs (explicit)

- **Latency:** compound queries cost 2 LLM calls. Mitigated by prompt
  caching if system prompt ≥1024 tokens (Phase 1 measurement). Fast-path
  untouched.
- **Planner discipline:** two-phase trigger is a prompt judgment. Eval
  gates rollout at ≥90% accuracy.
- **Backwards compatible:** the one-hop path still exists. Prompt revision
  is env-flagged (`planner_prompt_revision`) so rollback is a config
  change, not a redeploy.
- **Frontend:** prose-above-widget render order is a P0 gate, not a
  post-hoc verification. SSE ordering guarantees arrival order but not
  render order; React batching and scroll anchoring can reorder.

## Phasing

- **Phase 0 (ship blockers, must pass first):**
  - P0.1 Frontend prose-above-widget render order verified (Playwright).
  - P0.2 `present_widget()` mechanics unit tests in place.
  - Prompt-revision env flag wired. Today's prompt frozen as the rollback
    baseline.

- **Phase 1 (closes the "why" gap + cost mitigation):**
  - Prompt rewrite with three shapes + empty-KB guidance + fast-path hard
    rule.
  - Router change: preserve `AIMessage.content` on presenter branch.
  - Two-phase hop guard (max 2 phases, then force-terminate).
  - Error-path handling: turn-1 errors flow to turn-2 narration.
  - Three-valued `response_shape` in turn_summary.v1.
  - **Content-before-tool-calls streaming order verified** (Verification
    item 6). This is a distinct streaming-protocol assumption from P0.1
    (which verifies frontend render order given correct upstream order).
    Must pass before the prompt change goes live. If the test fails,
    remediate per the options in Verification §6 — default to adding a
    prompt instruction, escalate to structured-output mode only if that
    is unreliable.
  - **Prompt caching verified** — confirm system prompt is ≥1024 tokens
    (gpt-5 auto-cache threshold). Cache hit on turn 2 is the key latency
    mitigation for compound queries; without it we pay 2× LLM cost AND
    2× tokens. If prompt is too short, pad with stable content (Tool list
    is stable; profile section is not). Log `cached_tokens` from OpenAI
    usage payload in `[llm_call.v1]` so we can measure hit rate.

- **Phase 2 (eval-gated rollout):**
  - Run the 100–150-row eval against the Phase 1 prompt.
  - Achieve ≥90% classification accuracy, 100% no-invented-policy.
  - One week **soft launch** (100% traffic on the new prompt, no auto-alerts,
    dashboards reviewed daily; see Observability §Soft-launch for details).
    Dual-running A/B is out of scope for this plan.
  - Set alerting thresholds from observed distribution (not a guess).

- **Phase 3 (followups and extensions):**
  - Sub-agent compound narration for transfer/refund rejection flows
    ("here's WHY this transfer was denied") — same prompt pattern applied
    inside sub-agent graphs. Out of scope for this plan; tracked separately.
  - Tune per-turn narration length cap if Planner starts dumping KB chunks.
  - Voice-channel compound path verification (prose is already the only
    surface in voice; confirm two-phase narration is TTS-ready).

## Followups (out of scope, noted here for tracking)

- **Sub-agent rejection narration.** Transfer and refund sub-agents return
  terminal widgets today. A denied transfer shows the result but no
  explanation. Applying the compound pattern inside the sub-agent graph
  (response_node with both prose and widget output) would close the
  parallel gap. Estimate: same lift as this plan, separate PR.
- **Per-turn narration length cap.** Prompt-level soft limit (e.g., "keep
  explanations under 3 sentences unless the user asks for more"). Ship
  only if eval shows the Planner over-narrating.
- **Multi-phase gather.** Hard 2-phase cap is conservative. If the Planner
  regularly needs two rounds of tool gathering before it can narrate
  (e.g., knowledge_search result prompts a follow-up data lookup),
  relax the cap to 3. Measure before relaxing.

## Open questions

- Is our system prompt already above the 1024-token cache threshold under
  gpt-5? If yes, caching is free. If no, Phase 1 includes a padding
  strategy. Needs a token count before implementation.
- Should we distinguish `llm_calls_from_cache` vs `llm_calls_uncached` in
  turn_summary? Only useful if Phase 1's prompt-caching measurement
  shows meaningful variance.

## Related work

- Widget architecture plan (`~/.claude/plans/crispy-baking-peacock.md`) —
  this plan extends the Planner/Presenter contract from that design.
- Regulatory grounding rule (in `enrichment.py`) — already requires
  `knowledge_search` for policy/regulation queries. This plan operationalizes
  that: turn 1 MUST call `knowledge_search` for "why" questions, which makes
  the grounding rule enforceable at the routing level.

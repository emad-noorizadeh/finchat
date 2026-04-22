# FinChat Orchestrator Architecture

Channel-aware LangGraph orchestrator with a **Planner + deterministic Presenter split**, channel-specific sub-agents, deferred tool loading, and an SSE streaming layer.

> Widget catalog and the 4-rule Presenter engine are documented in detail in [`widgets.md`](./widgets.md). Read that alongside this doc if you're working on the rendering path.
>
> The three-turn-shape response strategy (fast-path / two-phase / no-widget) and the hop-guard mechanism are specified in [`compound_response_plan.md`](./compound_response_plan.md). Read that if you're changing the Planner's response prompt or the `post_tool_router` branches.

## Graph Shape

Five nodes with conditional routing. Entry: `enrich`.

```
            ┌──────────┐
            │  enrich  │  build system prompt, refresh always-load tools, reset per-turn state
            └────┬─────┘
                 ▼
            ┌────────────┐
            │ planner_llm│  main LLM — data tools + present_widget
            └────┬───────┘
                 │  should_route()
       ┌─────────┼────────────┐
       ▼         ▼            ▼
      END   tool_execute    END     (text_fast_path / max_iterations)
             │
             │ post_tool_router()
       ┌─────┼──────────┬──────────────────┬──────────┐
       ▼     ▼          ▼                  ▼          ▼
      END  planner_llm  presenter  hop_guard_fallback END
           (ReAct loop) (rules →   (two-phase cap    (widget already
                        build →    hit — emit        emitted by sub-agent
                        emit)      text_card         tool / glass / final)
                                   widget, end)
```

Defined in `app/agent/graph.py`. Compiled with an optional checkpointer so state persists across HTTP turns (used by the resume / interrupt flow).

**The Presenter is a deterministic rules engine, not an LLM.** Widget decisions are made from `state.variables` against `WIDGET_CATALOG` — no model call in the rendering path. See `widgets.md` for the four rules.

**Why two routers:** `should_route` runs right after `planner_llm` — before `tool_execute` — so it can only distinguish "text" vs "has tool calls" vs "max iterations." `post_tool_router` runs after `tool_execute` has written `state.variables` and can see whether sub-agent-emitted widgets already terminated the turn, then decide Presenter vs ReAct loop.

## State — `AgentState`

File: `app/agent/state.py`

| Field | Purpose |
|---|---|
| `messages` | Conversation history, `add_messages` reducer (append-only with dedup) |
| `user_id`, `session_id` | Routing and persistence keys |
| `channel` | `"chat"` \| `"voice"` — read by every node, pinned per session at the UI layer |
| `available_tools`, `tool_schemas` | Tools currently bound to the LLM; grows mid-conversation when `tool_search` discovers new ones |
| `iteration_count` | Loop guard against infinite tool-calling (`settings.max_agent_iterations`) |
| `enrichment_context` | Assembled system prompt: base prompt + voice style clause when `channel=="voice"`. Rebuilt each turn. |
| `base_system_prompt` | Base prompt text (profile + memory facts + tool-search guidance). Built once per session. |
| `knowledge_sources` | Citations populated by tools returning `ToolResult(sources=...)`. Reset to `[]` each turn by `enrich()`. Appended to the final response as a Sources block (chat channel only). |
| `search_tool_calls` | Circular-discovery guard — counts `tool_search` invocations |
| `response_terminated` | `True` → skip LLM paraphrase, go to END. Set by `tool_execute` for sub-agent widgets / glass / `final=True`; set by the Presenter when it emits. |
| `last_executed_tools` | Names of tools executed in the most recent `tool_execute` step. Reset each turn by `enrich()`. Read by `planner_llm` to inject each tool's `response_instructions` onto the system prompt on the follow-up iteration. |
| `variables` | Data-tool output slots keyed by `tool.output_var` (e.g., `profile_data`, `accounts_data`). Reset per turn by `enrich()`. Read by the Presenter's rules engine. |
| `variables_order` | Slot → monotonic order-number written by `tool_execute` when a slot is populated. Used by Presenter rule 3 for section ordering. Reset per turn. |
| `variables_counter` | Monotonic within-turn counter incremented by `tool_execute`. Reset per turn. |
| `go_to_presenter` | Set by `tool_execute` when a sub-agent returns `ToolResult(go_to_presenter=True)`. Routes into Presenter on namespaced sub-agent slot writes. |
| `hop_guard_triggered` | Set by `hop_guard_fallback` when the two-phase cap fires. Reset per turn by `enrich()`. Surfaced in `[turn_summary.v1]`. |

## Nodes

### `enrich` (`app/agent/nodes.py`)

Runs once at turn entry.

- **First turn of a session** (`base_system_prompt` not yet in state):
  - Builds `base_system_prompt` via `EnrichmentService.build_system_prompt()` — user profile + memory facts + response-strategy clause for the Planner. Expensive; cached.
- **Every turn**:
  - Rebuilds `tool_schemas` / `available_tools` for always-loaded tools by calling each tool's `to_openai_schema()` fresh. Important because some tools (notably `knowledge_search`) have a dynamic `description()` tied to state outside the graph (the on-disk KB descriptor).
  - Cross-turn preservation: deferred tools discovered by `tool_search` in prior turns carry forward.
  - Mid-turn discovery happens inside `tool_execute`, not `enrich`.
  - If `channel == "voice"`, appends the voice style clause to `enrichment_context`. Tool-authored glass passes through verbatim.
  - Resets per-turn state: `iteration_count=0`, `search_tool_calls=0`, `response_terminated=False`, `knowledge_sources=[]`, `last_executed_tools=[]`, `variables={}`, `variables_order={}`, `variables_counter=0`, `go_to_presenter=False`, `hop_guard_triggered=False`.

`enrich()` does NOT retrieve from Chroma. Knowledge retrieval is the `knowledge_search` tool's responsibility.

### `planner_llm` (`llm_call` in `app/agent/nodes.py`)

- Reads `enrichment_context` into a `SystemMessage`.
- Appends bound tools' `workflow_instructions` (always-on while bound) and any `response_instructions` for tools that ran in the previous iteration (from `last_executed_tools`).
- **Context compaction** — runs `_trim_messages_for_llm(state["messages"])` before `ainvoke` so old tool-result bodies are collapsed into placeholders without breaking `tool_call_id` linkage. See *Context compaction* below.
- Binds `tool_schemas` via `llm.bind_tools()` — data tools + `present_widget` + `tool_search` + `knowledge_search` + `request_confirmation`. No render tools exist any more; nothing to filter.
- `ainvoke(messages)` returns an `AIMessage`, possibly with `tool_calls`.
- Increments `iteration_count`.

The Planner's job: gather data and decide between writing prose directly or handing off to the Presenter via `present_widget()`. The system prompt nudges it to emit a data tool AND `present_widget()` in the same message (parallel tool calls) when it knows the turn is a widget turn — deterministic fast path. A governance layer on top (see *Grounding + Scope policy*) gates what the Planner is allowed to answer ungrounded.

### `tool_execute` (`app/agent/nodes.py`)

Runs every tool call in the most recent `AIMessage`. Concurrency-safe tools run via `asyncio.gather`; others sequentially.

For each call:

1. `tool.execute(args, context)`. `context` carries `user_id`, `session_id`, `channel`, `available_tools`, `search_tool_calls`, and `variables`.
2. If the result is a `ToolResult`:
   - **Chat + `widget`** (sub-agent emission, e.g., `transfer_confirmation`): persists a `WidgetInstance` row, dispatches `dispatch_custom_event("widget", payload)`, marks `terminated = True`.
   - **Voice + `glass`**: dispatches `dispatch_custom_event("final_response", {"content": glass, "channel": "voice"})`, marks `terminated = True`.
   - **Chat + `glass` (no widget)**: same `final_response` path.
   - **`final=True`**: forces `terminated = True`. `ToolResult.__init__` rejects `final=True` without widget or glass.
   - `to_llm` (never displayed) is wrapped in a `ToolMessage` and added to history.
3. **Data tools with `output_var`**: writes `state.variables[output_var]` using `ToolResult.slot_data` if provided (full render-ready payload), else parses `to_llm` as JSON. Increments `state.variables_counter` and records `state.variables_order[output_var]`. That order-number drives Presenter rule 3 section ordering.
4. **`tool_search` discovery**: discovered tool names/schemas appended to `available_tools` / `tool_schemas`.

Widget emission in `tool_execute` today serves **only sub-agent widgets** (e.g., `TransferAgentTool` wraps a sub-agent's widget as `ToolResult(widget=...)`). Main-orchestrator widgets all flow through the Presenter.

### `presenter` (`app/agent/presenter.py`) — deterministic, terminal

Runs when `post_tool_router` picks the `presenter` branch. No LLM. Pure Python rules engine.

1. `select_render(state)` evaluates four rules in order (see `widgets.md` §4 rules):
   - **Rule 1 — Designed composite.** Populated slot set exactly matches some catalog entry's `slot_combination`. Tiebreak: catalog declaration order.
   - **Rule 2 — Single slot.** Exactly one populated slot maps to a catalog entry via `default_data_var`.
   - **Rule 3 — Generic composite.** 2+ populated composable mapped slots → `render_generic_composite`, sections ordered by `composite_priority` then `variables_order`, capped at 3 (overflow logged).
   - **Rule 4 — Text-card fallback.** Anything else → `text_card` with Planner prose OR per-slot `widget_to_llm` summaries.
2. Invokes the chosen `render_fn` with pre-resolved kwargs from `slot_arg_map`.
3. Persists a `WidgetInstance`, dispatches `widget` SSE event, sets `response_terminated=True`.
4. Graph edge `presenter → END` — no follow-up `tool_execute` hop.

**Observability:** each run logs `[presenter_choice] rule=... widget=... slots=[...]`. Rule-3 truncations log `[presenter_truncate]`. Designed-composite near-misses log `[presenter_designed_composite_missed]`.

### `hop_guard_fallback` (`app/agent/nodes.py`) — terminal safety valve

Runs when `post_tool_router` detects the two-phase cap has been hit:
`iteration_count >= 2` AND `response_terminated=False` AND the Planner's
latest AIMessage has no `present_widget()` tool-call. That shape means the
Planner has already made two LLM calls and is trying to start a third
gather phase — either stalling or trying to narrate-and-loop.

- Emits a generic `text_card` widget ("Sorry — I got stuck") via
  `dispatch_custom_event("widget", ...)`. Voice channel emits a short
  prose apology via `final_response` instead.
- Persists the widget so the frontend sees the same envelope as any
  other widget.
- Sets `response_terminated=True` and `hop_guard_triggered=True`. Edge
  `hop_guard_fallback → END`.

The hop cap is deliberately conservative. A third Planner call is
frequently wasteful (re-gathering the same data, or talking around the
answer) but occasionally legitimate (KB result prompts a follow-up data
lookup before narration). The fallback is a safety valve, not a
correctness guarantee — observability (see below) distinguishes
"Planner stalled" from "Planner wanted legitimate turn 3" so we can
tune the cap based on real traffic.

**Observability:** `post_tool_router` logs
`[hop_guard_triggered] iteration=N intended_tools=[...] content_len=N`
before routing to the fallback node. `content_len > 0` means the Planner
was narrating and looping (legitimate intent, candidate for relaxing
the cap). `content_len == 0` means a pure tool-loop stall (prompt
tightening signal).

## Routing

### `should_route` — after `planner_llm`

- `iteration_count >= max_agent_iterations` → `END` (safety).
- No `tool_calls` → `END` (text fast-path; prose already streamed).
- Any `tool_calls` → `tool_execute`.

Logs each decision as `[router_decision] branch=<name> ...`.

### `post_tool_router` — after `tool_execute`

Four branches, explicit precedence:

1. `response_terminated == True` → `END`. A sub-agent widget, voice glass, or `final=True` tool already terminated the turn.
2. `go_to_presenter == True` → `presenter`. Sub-agent returned in `to_presenter` mode with namespaced slot writes.
3. Planner's latest `AIMessage` includes `present_widget()` → `presenter`.
4. `iteration_count >= 2` AND none of the above → `hop_guard_fallback`. Two-phase cap hit — the Planner is starting a third gather phase without handing off. Emits a generic text_card and ends.
5. Otherwise → `planner_llm` (ReAct loop — data tools gathered information, the Planner can now synthesize or call more tools).

The `presenter` branch absorbs both the "serialized data-then-present" and "parallel data-with-present" shapes; the rules engine reads `state.variables` and picks the same render deterministically. The two-phase cap (`_TWO_PHASE_HOP_CAP = 2` in `nodes.py`) is the soft ceiling on compound "why" queries — see the *Compound response — three turn shapes* section below.

## Tool Contract

### `BaseTool` (`app/tools/base.py`)

| Attribute | Role |
|---|---|
| `name` | Unique identifier |
| `should_defer` / `always_load` | Exactly one must be true. Always-loaded tools are bound from turn 1; deferred tools are discovered via `tool_search`. |
| `search_hint` | Keywords used by `tool_search` for weighted matching |
| `is_read_only`, `is_concurrency_safe` | Execution policy |
| `workflow_instructions` | Multi-step guidance; injected into the system prompt only while the tool is bound |
| `response_instructions` | Post-tool guidance; injected into the system prompt on the iteration immediately after this tool ran. |
| `channels` | Tuple of channels where this tool is available. Default `("chat",)`. Tools serving both set `("chat", "voice")`. |
| `has_glass` | `True` if the tool can produce a glass string (TTS/display-ready final text). Optional. |
| `output_var` | If set, `tool_execute` writes the parsed `to_llm` JSON (or `ToolResult.slot_data` if provided) into `state.variables[output_var]`. Drives the Presenter's rules engine. |
| `widget`, `flow`, `validations`, `errors` | Self-describing metadata |

There is no `is_render` attribute. Widget rendering is handled by the Presenter's rules engine, not by LLM-bound tools.

### `ToolResult` (`app/tools/base.py`)

| Field | Role |
|---|---|
| `widget` | Visual UI payload; chat-primary. Used by sub-agent widget emission path only. If set without `to_llm`, a summarizer derives `to_llm`. |
| `to_llm` | Concise text for LLM reasoning. Never shown to the user. |
| `slot_data` | Optional — if set, `tool_execute` writes this (not parsed `to_llm`) into `state.variables[output_var]`. Lets a tool send a compact summary to the LLM while preserving the full render-ready dataset for the widget. |
| `glass` | Display-ready text. TTS-ready in voice; plain text in chat. |
| `final` | If `True`, the graph terminates after this tool — the LLM does not paraphrase. Requires `widget` or `glass` (validated in `__init__`). |
| `sources` | List of `{title, url}` citations. Accumulated into `state.knowledge_sources` (dedup by URL); chat renders as a Sources block, voice suppresses. |

Channel × `ToolResult` behavior (enforced in `tool_execute`):

| Channel | `widget` | `glass` | `final` | Effect |
|---|---|---|---|---|
| chat | set | — | — | Widget event, terminate (sub-agent path) |
| chat | — | set | — | `final_response` event, terminate |
| chat | — | — | — | Normal: `to_llm` → LLM paraphrase or Presenter hands off |
| voice | — | set | — | `final_response` event, terminate. Widget (if any) suppressed. |
| voice | — | — | — | Normal: `to_llm` → LLM paraphrase under voice style clause |
| any | set/glass | set/glass | `True` | Terminate regardless |

### LLM variants (`app/services/llm_service.py`)

All chat-model access goes through `get_llm(variant="primary")`. No code outside `llm_service.py` instantiates `ChatOpenAI` directly. A **variant** is a named model configuration (model + temperature + max_tokens). Model names come from `settings` so operators can override via `.env`.

| Variant | Purpose | Model setting (default) | Default callers |
|---|---|---|---|
| `primary` | Planner LLM node; default for unspecified callers | `settings.llm_model` (`gpt-5`) | `app/agent/nodes.py:llm_call` |
| `sub_agent` | Sub-agent `parse_node(mode=llm)` + `llm_node` calls — structured extraction at lower temperature | `settings.sub_agent_llm_model` (`gpt-4.1`) | `app/agents/parsers/__init__.py:llm_parse`, `app/agents/nodes/llm_node.py` |

**Reasoning-model handling.** Models in the `o1 / o3 / o4 / gpt-5*` families reject any `temperature` other than 1. `get_llm` detects them by name (`_REASONING_MODEL_PATTERN = r"^(o[1-9]\b|gpt-5)"`) and omits `temperature` from the `ChatOpenAI` constructor so the API uses its default. `max_tokens` is still passed — `langchain-openai` translates it to `max_completion_tokens` internally for reasoning models. Operators can also force this behaviour by setting `is_reasoning: True` in the variant dict, independent of the model name.

Sub-agent templates opt into a variant per parse/llm node via the template JSON:

```json
{"id": "parse_open", "type": "parse_node",
 "data": {"mode": "llm", "llm_variant": "sub_agent", "system_prompt": "…", "output_schema": {…}}}
```

Every sub-agent LLM call is also tagged `subagent_internal` via `RunnableConfig.tags`. The chat SSE router (`app/routers/chat.py`) filters `on_chat_model_stream` events carrying that tag so the parse node's raw JSON doesn't leak into the user-visible stream.

The Presenter does not call the LLM. Any future variant added solely for Presenter use would live in this table, but isn't needed today. Instances are cached per variant inside `llm_service.py`; calling `get_llm(name)` is safe and cheap. `reset()` clears the cache.

### Knowledge-retrieval telemetry

`RAGService.build_knowledge_context_with_sources()` emits a structured log line on every retrieval:

```
[kb_retrieval] query='what is a credit score' top_score=0.872 results=5 file_fallback=True
```

Pair with the `[tool_result_size]` warning (`app/agent/nodes.py:tool_execute`) to answer the two questions that gate any future knowledge_search optimization:

- How often is `top_score` in the weak band (0.3–0.5)? → signal for adding a relevance gate.
- How often does `tool_result_size` fire on `knowledge_search`? → signal for adding a compression step.

Both steps were explicitly deferred; the data decides when to ship.

### Registry filters (`app/tools/__init__.py`)

- `get_always_load_tools(channel)` — `always_load` tools whose `channels` include `channel`; if the tool is agent-backed, the agent must have a variant for that channel.
- `search_tools(query, channel)` — same channel filter applied to `should_defer` tools; weighted match against `name` (5x), `search_hint` (3x), combined overlap (1x).

Always-loaded tools today: `tool_search`, `get_profile_data`, `get_accounts_data`, `get_transactions_data`, `knowledge_search`, `present_widget`, `transfer_money`, `refund_fee`. Deferred tools — if any — are discovered on demand via `tool_search`. `transfer_money` and `refund_fee` are Planner entry points that drive channel-specific sub-agent templates (see *Sub-agents (v4)* below).

## Grounding + Scope policy (regulated-finance governance)

This app runs in a regulated financial context, so the Planner prompt carries a governance layer that constrains what answers the LLM is allowed to produce. Two complementary rules; both live in `app/services/enrichment.py:build_system_prompt()` and are reinforced by `app/tools/knowledge_search.py:workflow_instructions`.

### Scope — defined by *capability*, not topic

The Planner's scope is anchored on what the live catalogue can actually do:

1. **The user's own data** — data tools bound to the LLM (`get_profile_data`, `get_accounts_data`, `get_transactions_data`, …).
2. **Actions** — action tools / sub-agents bound to the LLM (`transfer_money`, `refund_fee`, plus any dynamically registered sub-agents from the Agent Builder).
3. **Knowledge-base lookups** — any factual question → `knowledge_search`; the KB descriptor inside the tool description lists topics that are actually indexed.
4. **Conversational / meta** — greetings, thanks, small talk, "who are you", "what can you do" — answer in your own voice.

If a request doesn't map to any of (1)–(4), it's out of scope. The Planner politely declines and redirects — **one short sentence, warm and a little witty, never a multi-paragraph refusal**. It does NOT answer the out-of-scope question even partially, and it does NOT invent a tool or KB article. Ambiguous queries get ONE clarifying question instead of a guess.

**Capability changes over time.** The prompt explicitly tells the LLM not to treat a topic as permanently out-of-scope just because today's catalogue doesn't cover it. When a future tool or sub-agent is added (e.g., a market-data tool, a new AgentBuilder flow), the in-scope surface automatically expands — no prompt edit required. If the LLM is unsure, it can call `tool_search` or `knowledge_search` to check.

Observed behaviour: *"what's the price of gold today"* → *"Live commodity quotes aren't in my wheelhouse — I stick to your accounts and banking tasks. Want to review your recent transactions or set up a transfer?"* (text_fast_path, no tool call).

### Regulatory grounding — mandatory `knowledge_search` for financial questions

For any factual question about money / banking / credit / cards / saving / loans / investing / etc., the Planner MUST:

1. Call `knowledge_search(query=…)` first.
2. Paraphrase only from its returned context.
3. Never fall back to training-data knowledge. If the KB returns "No relevant documents found…", reply plainly: *"I don't have specific guidance on this in our knowledge base — please reach out to a specialist."*

The rule is stated in two independent places so the LLM can't miss it:

- **Base system prompt** (`enrichment.py`) — REGULATORY GROUNDING RULE section, directly above the Planner's response-strategy guidance.
- **`knowledge_search.workflow_instructions`** — injected into the system prompt every turn the tool is bound. Uses MUST / MUST NOT language and tells the LLM *"a wasted query is cheaper than an ungrounded answer."*

The SSE stream appends a Sources block to the user's response (chat only; voice suppresses) from `state.knowledge_sources`. The Planner is instructed NOT to invent its own "Sources" section — duplication would confuse the reader.

Observed behaviour: *"how can I improve my credit score"* → `knowledge_search` called → response paraphrases the indexed "Improving Your Credit Score" article → Sources block auto-appended listing every relevant KB entry.

### Governance gotchas

- `state["base_system_prompt"]` is **cached per session** in the checkpointer. Prompt edits apply to **new chats** immediately; existing sessions continue to carry their old copy until a new chat is started. If you need to invalidate all live sessions, add a prompt-version field and bust the cache on mismatch in `enrich`.
- The governance layer is advisory, not enforced — a determined LLM could still drift. Defensive options: a post-hoc guardrail in `post_tool_router` that rejects any text-only response mentioning regulated terms without sources attached, or a second LLM call to score compliance. Not implemented today.

## Context compaction (Phase 1)

`app/agent/nodes.py:_trim_messages_for_llm()` caps per-turn LLM latency as sessions grow. Runs inside `llm_call` immediately before `ainvoke` — the trimmed list is ephemeral; `state["messages"]` is never mutated, so the checkpointer keeps the full history for audit + resumption.

### Policy

- **Always keep the last 5 user turns intact** end-to-end.
- For older turns, **collapse any `ToolMessage` whose content > 600 chars** into a placeholder: `[older tool result for <tool_name> omitted — <N> chars]`. The ToolMessage object stays; only its `content` is replaced. `tool_call_id` remains wired to the preceding `AIMessage.tool_calls`, so LangChain's tool-calling validator is satisfied.
- Small old tool results are preserved untouched (still under the 600-char threshold).
- `HumanMessage` and `AIMessage` content (user turns + assistant prose) are **never touched** — the LLM can always reason about what the user asked, just not about the full body of old tool outputs.

Tunables (currently hard-coded in `nodes.py`):
- `_TRIM_KEEP_RECENT_TURNS = 5`
- `_TRIM_TOOL_PAYLOAD_CHARS = 600`

### Why this shape

Tool messages are the single biggest driver of context growth (`knowledge_search` returns 2–5 kB, data tools 300–3 kB). Collapsing them saves the majority of the bytes without losing the conversational structure. The LLM can still tell *that* a tool ran for an earlier turn, and if the user references old data the Planner re-calls the tool — cheap, and the answer is guaranteed fresh.

### Observability

Every trim fires a log line: `[context_trim] trimmed_tool_results=N chars_saved=N total_messages=N kept_turns=5`. Grep to see how often and how much compaction fires in production. If you see consistent chars_saved > 20k, that's the signal to graduate to Phase 2 (rolling summary via a second `sub_agent`-variant LLM call).

### What's NOT in Phase 1 (deliberate)

- **No summarization** — no second LLM call. Keeps Phase 1 zero-cost.
- **No tiktoken-based absolute token cap** — the turn-count heuristic is sufficient while `gpt-5`'s context is roomy. Adds a dependency we don't need yet.
- **No `AIMessage.content` trim** — assistant prose is small relative to tool outputs.

### Smoke-tested behaviours

Proven via unit + integration tests (captured LLM-input via mocked `ainvoke`):

| Scenario | Behaviour |
|---|---|
| Session ≤ 5 turns | Identity pass-through — returns the exact same list object (no copy) |
| Session = 8 turns, all big tool results | 3 oldest tool messages collapsed, 5 recent intact, 0 orphan `tool_call_id`s |
| Small old tool results | Preserved (< 600 chars) |
| User prose + assistant prose | Untouched |
| `state["messages"]` | Never mutated — checkpointer keeps full history |

End-to-end via the chat UI: after 11 user turns (well past the cutoff), *"what was the first thing I asked you in this session?"* → the LLM correctly recalled turn 1 despite that turn's tool result having been collapsed. Conversational coherence holds because user `HumanMessage.content` is never trimmed.

## Knowledge retrieval

Single LLM-directed path via the `knowledge_search` tool (`app/tools/knowledge_search.py`). There is no auto-RAG in `enrich()`.

**Flow:**
1. On every turn, `enrich` rebuilds `tool_schemas`. `knowledge_search.description()` reads `data/kb_descriptor.txt` and appends it to the tool description, so the LLM sees a running summary of what the KB contains.
2. If the LLM decides the turn is knowledge-relevant, it calls `knowledge_search` with a self-contained `query` argument. It is responsible for rephrasing contextual follow-ups (*"tell me more"* → *"credit mix factors"*) using the conversation history it already has.
3. The tool calls `RAGService.build_knowledge_context_with_sources()`, which does 2-stage retrieval over the `system_knowledge` Chroma collection (vector similarity + keyword-overlap boost + optional full-file fallback). Returns `(context_text, [{title, url}, ...])`.
4. The tool returns `ToolResult(to_llm=context_text, sources=sources_list)`. `tool_execute` dedup-merges the sources into `state.knowledge_sources` (across multiple calls in the same turn).
5. The LLM paraphrases `to_llm` into its final answer. In chat, the SSE layer appends a Sources block at the end (`**Sources**\n- [title](url)`). In voice, the Sources block is suppressed (markdown is bad for TTS) — `app/routers/chat.py` guards this.

**KB descriptor persistence** (`data/kb_descriptor.txt`):
- Generated at upload and delete time by `IndexingService._refresh_kb_descriptor()`, which calls `RAGService.rebuild_kb_descriptor()` → writes the file.
- Bootstrapped on app startup (`app/main.py` lifespan) if missing — so an existing Chroma collection installed before this feature is not invisible to the LLM.
- Read per turn by `knowledge_search.description()` via `RAGService.read_kb_descriptor()` — a ~200-byte file read, no Chroma query. Always in sync with what's actually indexed; no cache, no TTL.

## The cycle — how iteration works

`planner_llm` ↔ `tool_execute` is a ReAct-style loop that can run multiple times within a single user turn. Turn termination comes from one of four places: the Planner writes prose, the Presenter emits a widget, a glass/`final=True` tool terminates, or the iteration guard fires.

### Termination exits

| Exit | Trigger | Router |
|---|---|---|
| **Planner text** | Planner's AIMessage has no `tool_calls` | `should_route` returns `"text_fast_path"` → END |
| **Presenter widget** | Planner called `present_widget()`; rules engine runs, emits widget, ends | `post_tool_router` returns `"presenter"` → `presenter` sets `response_terminated=True`, edge to END |
| **Sub-agent widget / glass / final** | `tool_execute` emitted via `ToolResult(widget=...)` / `ToolResult(glass=..., final=True)` | `post_tool_router` sees `response_terminated=True` → END |
| **Two-phase hop guard** | `iteration_count >= 2` with no `present_widget()` handoff | `post_tool_router` returns `"hop_guard_fallback"` → fallback emits text_card, sets `response_terminated=True`, edge to END |
| **Iteration guard** | `iteration_count >= max_agent_iterations` (default 15) while Planner keeps asking for tools | `should_route` returns `"end"` |

### Walkthrough: single-widget query

User message: *"show my profile"*. Channel = chat.

```
enrich
   • Rebuild always-loaded schemas: tool_search, knowledge_search,
     get_profile_data, get_transactions_data, present_widget.
   • Reset knowledge_sources=[], variables={}, variables_order={},
     variables_counter=0, response_terminated=False, iteration_count=0.

planner_llm (iteration 1)
   • Emits: tool_calls = [get_profile_data(), present_widget()]  (parallel)
   • iteration_count = 1.

should_route → "tool_execute"

tool_execute
   • get_profile_data runs → ToolResult(to_llm='{...}', slot_data={...}).
     state.variables["profile_data"] = {...}.
     variables_counter = 1, variables_order["profile_data"] = 1.
   • present_widget runs (no-op sentinel) → ToolResult(to_llm="").
   • No widget, no glass from tool_execute → terminated=False.

post_tool_router:
   • response_terminated=False.
   • Planner's AIMessage had present_widget → "presenter".

presenter (deterministic)
   • select_render(state) → rule 2 (single mapped slot: profile_data → profile_card).
   • build_args = {"profile_data": <value>} (via slot_arg_map).
   • profile_card_widget(**build_args) → widget dict.
   • Persists WidgetInstance, dispatches "widget" event.
   • Returns {"response_terminated": True}.

edge: presenter → END.
```

Total LLM calls: **one** (Planner). Zero Presenter LLM cost. Total tool calls: 2 (data tool + present_widget no-op).

### Walkthrough: compound query — designed composite

User message: *"show me my profile and my accounts together"*. Channel = chat.

```
planner_llm (iteration 1 — or 2 if tool_search was needed for accounts)
   • Emits: tool_calls = [get_profile_data(), get_accounts_data(), present_widget()]

tool_execute
   • Runs all three in parallel.
   • state.variables has: profile_data, accounts_data.
   • variables_order: profile_data=1, accounts_data=2.

post_tool_router → "presenter" (present_widget present, response_terminated=False)

presenter
   • select_render(state) walks rules:
     - Rule 1: populated = {profile_data, accounts_data}. Catalog has
       profile_with_accounts with slot_combination=["profile_data", "accounts_data"].
       Exact set match → rule 1 fires.
   • build_args via slot_arg_map: {"profile": <profile>, "accounts": <accounts>}.
   • profile_with_accounts_widget(**build_args) → widget dict.
   • Emits "widget" event, response_terminated=True.
```

Total LLM calls: **one** (Planner). No Presenter LLM.

### Walkthrough: compound query — two-phase "why" with prose + widget

User message: *"why did I get a fee on my savings?"*. Channel = chat.
This query needs BOTH data (which fee) AND policy (what rule triggered
it) — the Planner should go two-phase, not fast-path.

```
planner_llm (iteration 1)
   • Emits: tool_calls = [get_transactions_data(query="fee", account="savings"),
                          knowledge_search(query="savings monthly fee policy")]
   • content = "" (gather phase — no narration yet).
   • NO present_widget() — that's the fast-path shape and would skip
     knowledge retrieval.

tool_execute
   • get_transactions_data runs → state.variables["transactions_data"] populated.
   • knowledge_search runs → state.knowledge_sources populated.
   • No widget, no glass → terminated=False.

post_tool_router:
   • response_terminated=False, no present_widget, iteration_count=1
     (below the 2-phase cap) → "planner_llm".

planner_llm (iteration 2)
   • Now sees the data + KB context in the message history.
   • Emits: content = "You were charged the $5 Savings Monthly Fee on
     03/31. This fee applies when the average daily balance falls
     below $500 during the statement period…"
     tool_calls = [present_widget()]   (optional — prose + widget compound)
   • iteration_count = 2.
   • OpenAI's streaming delivers content deltas before the tool_call
     delta — verified by tests/test_streaming_order.py. The prompt
     reinforces this order explicitly.

tool_execute
   • present_widget is a no-op sentinel.

post_tool_router → "presenter"

presenter
   • Rule 2 on transactions_data → transaction_list widget.
   • Emits "widget" event, response_terminated=True.
```

Total LLM calls: **two** (Planner turn 1 gathers; Planner turn 2
synthesizes). The SSE consumer renders the prose bubble above the widget
card in the same assistant message group.

If turn-1's `knowledge_search` returns no match, the Planner's turn-2
prose narrates from data alone and explicitly flags the gap. It does
NOT invent policy text. The prompt enforces this.

### Walkthrough: hop guard force-terminates

User query needing synthesis, Planner gets confused and loops without
signalling. Graph keeps state moving to iteration 2+ without a
`present_widget()` handoff.

```
planner_llm (iteration 1) → emits data-tool calls
tool_execute → data gathered, no widget, no termination
post_tool_router → iteration=1, no present_widget, below cap → planner_llm

planner_llm (iteration 2) → emits MORE data-tool calls (still no present_widget)
tool_execute → more data, no widget, no termination
post_tool_router → iteration=2, no present_widget, AT cap → "hop_guard_fallback"
   Logs: [hop_guard_triggered] iteration=2 intended_tools=[…] content_len=N

hop_guard_fallback
   • Emits text_card widget: "I'm having trouble synthesizing a complete
     answer from the available data. Could you rephrase or narrow what
     you're asking?"
   • response_terminated=True.
```

The `hop_guard_triggered` flag + `content_len` field drive tuning
decisions — see *Observability* below.

### Walkthrough: contextual follow-up

Turn 1: *"what is a credit score?"* → Planner calls `knowledge_search`, paraphrases, Sources block appended. Text fast-path — Presenter never invoked.

Turn 2: *"how about improving it?"* — checkpointer restores `AgentState`. New user message lands in `messages`. `enrich` resets per-turn fields. Planner rephrases the follow-up, calls `knowledge_search` again. Text fast-path.

### Iteration guardrails

- **`max_agent_iterations`** (default 15, `app/config.py`) caps the loop. `should_route` forces an END if the Planner keeps asking for tools without concluding.
- **`search_tool_calls`** caps `tool_search` at 2 per turn — prevents the LLM from thrashing through tool discovery without acting on discovered tools.
- **`ToolResult(final=True)`** requires widget or glass in `__init__`. A tool cannot silently terminate.
- **Checkpointer** persists `AgentState` between turns keyed by `session_id`, so iteration counters and discovered tools carry forward only *within* a turn (counters reset in `enrich`, but `available_tools` can keep previously discovered tools).

### Synthesis — where the final user-facing text is written

Two exit paths produce user-visible output.

**Path A — LLM-authored synthesis.** Last `planner_llm` of a turn has no `tool_calls`; the LLM's content streams via `on_chat_model_stream` → SSE `response_chunk` events. Post-stream, the SSE layer appends a `**Sources**` block if knowledge_sources were accumulated (chat only).

**Path B — Tool/Presenter-authored finalization.** The Presenter (main widget path) or `tool_execute` (sub-agent widgets, glass, `final=True`) dispatches `widget` / `final_response` via `dispatch_custom_event`. `response_terminated=True` → graph ends. The LLM never gets the chance to paraphrase.

**Choosing between paths.** The Planner chooses — answering in prose without `present_widget()` takes Path A. Calling `present_widget()` routes to the Presenter (Path B). Sub-agents that emit widgets in their response also take Path B via `tool_execute`'s widget-emission code.

## Compound response — three turn shapes

The Planner picks one of three turn shapes per user query. The distinguishing
feature is whether it emits `present_widget()` and, if so, in which turn.
This section is the operational summary; see [`compound_response_plan.md`](./compound_response_plan.md) for the full design doc.

### Shape 1 — Fast path (one Planner turn, no narration)

Triggered by: "show me / list / what's my". The Planner emits the data
tool + `present_widget()` in the SAME message, content is empty.
Hard rule in the prompt: narration is forbidden on this path. The
answer IS the widget.

- Example: *"show my transactions"* → `[get_transactions_data(view="recent"), present_widget()]`.
- `response_shape=widget_only`.
- 1 LLM call, 1 widget event, no prose streamed.

### Shape 2 — Two-phase (gather → synthesize)

Triggered by: "why / how / explain / what caused" when answering needs
BOTH data AND policy (KB). The Planner gathers in turn 1 (data tools +
`knowledge_search`, NO `present_widget()`), loops back, then synthesizes
in turn 2 (narration prose + optional `present_widget()`).

- Example: *"why did I get a fee on my savings?"*
- `response_shape=prose_plus_widget` (if the Planner emits the widget
  in turn 2) or `no_widget` (if it narrates without a widget).
- 2 LLM calls. Prose streams first, widget renders below it.
- Turn-2 edge cases: empty KB → narrate from data, flag the gap; turn-1
  tool error → narrate what you have.

### Shape 3 — No-widget (zero or more tools, prose only)

Triggered by: data-reasoning "why" (no KB needed), single-fact queries,
general-knowledge explanations, conversational acks. The Planner calls
whichever tools it needs and narrates. No `present_widget()` ever.

- Example: *"why is my balance so high?"* → data tool + narrate.
- Example: *"what's a good savings rate?"* → `knowledge_search` + narrate.
- `response_shape=no_widget`.
- 1–2 LLM calls depending on tool usage.

### The hop-guard safety valve

The two-phase path is capped at TWO Planner LLM calls without a
`present_widget()` handoff. A third gather phase triggers
`hop_guard_fallback`. See that node's description above. The
`hop_guard_triggered` flag + the `content_len` observability field
distinguish "Planner stalled" from "Planner legitimately wanted turn 3."

### Planner prompt revision flag

The response-strategy section of `app/services/enrichment.py:build_system_prompt()`
is tagged with a revision string via `settings.planner_prompt_revision`
(env-overridable as `PLANNER_PROMPT_REVISION`). The value appears in
every `[turn_summary.v1]` log line so rollback analysis can compare
shape distributions across revisions.

Rolling back a prompt change is a config flip — no redeploy. Write a new
revision string into `.env`, restart, and the old revision's pinned
prompt text (whatever was shipped with it) is served. When a new
revision ships, bump the default in `config.py` to match.

## Sub-agents (v4) — file-based LangGraph templates

Deep dives: [`transfer_flow.md`](./transfer_flow.md) (Transfer), [`sub_agents.md`](./sub_agents.md).

Sub-agents are **JSON templates compiled to LangGraph `StateGraph`s**. No hand-coded classes, no 3-node default shape. Two sub-agents ship today: `transfer_money` (Transfer) and `refund_fee` (Fee Refund), each with per-channel variants (`<name>_chat`, `<name>_voice`).

### Node types (`app/agents/nodes/`)

Seven primitives a template can compose:

| Node | Role |
|---|---|
| `parse_node` | Extract values from the latest user message. Two modes: `regex` (registered parsers in `app/agents/parsers/`) and `llm` (structured-output `ChatOpenAI` call via `llm_parse`). Merges writes into `state.variables`. Tracks `parse_retry_count` per targeted slot. |
| `condition_node` | Dispatcher. Pure router — the compiler translates each outgoing edge's `predicate` (DSL in `app/agents/predicates.py`: `has/is_empty/==/!=/&&/‖/!`) into `add_conditional_edges`. First predicate to evaluate true wins. **No LLM.** |
| `tool_call_node` | Calls one `AgentTool` action with templated `params` (`{{variables.X}}`). Writes the result into `state.variables[output_var]`. Optional `post_write` resets stale flags on success. |
| `interrupt_node` | Pauses for a user reply. In the voice driver, sets `variables._pending_interrupt_payload` and terminates the inner graph cleanly; the outer driver translates that into a LangGraph `interrupt()` so the main orchestrator pauses too. |
| `llm_node` | Free-form LLM call with a scoped `system_prompt` and a bound tool subset. Used by non-regulated sub-agents. Regulated templates must declare `output_schema`. |
| `tool_node` | Executes tool_calls emitted by the previous `llm_node`. |
| `response_node` | Terminal. Four `return_mode`s: `widget` (chat), `glass` (voice), `to_presenter` (namespaced slot writes — blocked on regulated), `to_orchestrator` (text the Planner paraphrases). An `is_escape_target: true` response_node receives the runtime-injected priority-0 edge for abort / topic-change. |

### Template → LangGraph (compiler)

`app/agents/template_compiler.py:compile_template(template, checkpointer=None)` walks the template, registers each node's handler factory, and installs conditional edges per source. Three runtime-injected priority-0 rules:

- **Escape** — any `condition_node` routes to an `is_escape_target` response_node when `has(variables._escape_kind)`.
- **Retry-exhausted** — routes to the first `interrupt_node.data.on_retry_exhausted` (or a runtime-default) when `has(variables.retry_exhausted_for_slot)`.
- **Interrupt terminates the inner graph** — every `interrupt_node`'s outgoing edge is replaced with `→ END` at compile time. The outer driver restarts the inner graph from `entry_node` on each resume, feeding in accumulated messages + variables.

### State (`SubAgentState`)

Separate from main `AgentState`. Fields: `messages`, `user_id`, `session_id`, `channel`, `main_context` (carries `agent_name` for tool resolution), `variables` (scratchpad), `_terminal`, plus retry tracking (`parse_retry_count`, `last_prompted_slot`, `retry_exhausted_for_slot`).

### Storage — `SubAgentTemplate` SQL table + seed-from-files

`app/agents/template_store.py` + `app/models/sub_agent_template.py`. Templates live in SQLite; JSON files in `app/agents/templates/` are **seeds**:

- On first boot (empty DB), `seed_from_files()` imports every `*.json` as `source="seed"`, `status="deployed"`.
- On subsequent boots, files with a different hash than the DB row overwrite the seed row. **User-authored rows** (`source="user"`) are never touched.
- Regulated templates carry `locked_for_business_user_edit=true`. The write endpoints (`POST/PUT/DELETE /api/agents/...` in `app/routers/agents.py`) return 403 on those; the Agent Builder UI shows a 🔒 banner.

This gives two authoring paths:
- **Regulated** (Transfer, Refund) — edit JSON in repo, PR + code review, deploy re-seeds the DB row.
- **Non-regulated** (business-user sub-agents) — full CRUD through the Agent Builder UI, stored as `source="user"`.

### Planner entry points — `TransferAgentTool` / `RefundAgentTool`

Each sub-agent ships a thin `BaseTool` subclass that the Planner LLM calls (`transfer_money(message=...)`, `refund_fee(message=...)`). The tool:

1. Looks up the channel-specific template via `template_for_agent(agent_name, channel)`.
2. Compiles it to a `StateGraph` (cached with `@lru_cache`).
3. Drives the inner graph with the outer-interrupt loop + process-local accumulated state (`_INNER_STATE` dict in `app/agents/runtime.py`) for voice. Chat terminates in one pass.
4. Maps the terminal `response_node`'s `_return_mode` onto a `ToolResult`:
   - `widget` → `ToolResult(widget=…)` (chat) or `ToolResult(glass=widget_to_llm(…), final=True)` (voice suppresses widgets).
   - `glass` → `ToolResult(glass=..., final=True)`.
   - `to_orchestrator` → `ToolResult(to_llm=text)` — Planner paraphrases.

`response_terminated` is set at `tool_execute` when the sub-agent returns a widget/glass, so `post_tool_router` exits to END without invoking the Presenter.

### AgentTool — sub-agent-scoped tools with declared actions

`app/tools/agent_tool.py` defines `AgentTool`: a domain-service wrapper with a `@action` decorator that registers methods + `params_schema` + `output_schema`. A `tool_call_node` addresses it as `{tool: "<name>", action: "<action>"}`; the handler looks up `(agent_name, tool_name)` in `AGENT_TOOL_REGISTRY`.

Two AgentTools ship today:

- **`TransferOpsTool`** (`app/tools/transfer_ops.py`, `name="transfer"`, `agent_name="transfer_money"`) — actions `get_details`, `get_pair`, `get_options`, `validate`, `submit`, `resolve_account`. Wraps `TransferService`.
- **`RefundOpsTool`** (`app/tools/refund_ops.py`, `name="refund"`, `agent_name="refund_fee"`) — actions `list_fees`, `resolve_fee`, `submit_refund`. Wraps `RefundService`.

The `/api/tools?agent_name=<name>` endpoint returns per-tool `actions: [{name, description, params_schema, output_schema}]`. The Agent Builder's `ToolCallNodeEditor` uses this to populate its Tool + Action dropdowns and auto-generate param placeholders from `params_schema`.

### Interactive widgets with action handlers

Chat sub-agents can emit **three-stage widgets** whose user actions are handled server-side without re-entering the sub-agent graph:

| Widget | Stages | Handler |
|---|---|---|
| `transfer_form` | `form → review → completed` | `_handle_transfer_validate` → `_handle_transfer_submit` → `_handle_transfer_back` / `_handle_transfer_*` in `app/widgets/actions.py` |
| `refund_form` | `select_fee → review → completed` | `_handle_refund_select` → `_handle_refund_submit` → `_handle_refund_back` |

Each stage:
- User clicks a button → `POST /api/widgets/{instance_id}/action` with `action_id` + `payload`.
- `app/routers/widgets.py` routes to `app/widgets/actions.py:handle_action`, which dispatches to the right per-widget handler.
- Handler mutates `instance.data` (including `_stage` flag), optionally flips `status` (`completed` / `dismissed`). Returns the refreshed instance; the client re-renders the widget with the new state.
- DENIED refund outcomes keep `status="completed"` (the request flow finished; the outcome is negative, not retryable). The widget's own card surfaces APPROVED vs DENIED.

Widget context (`user_id`, `session_id`) is stamped into `instance.extra_data` at creation time (`app/agent/nodes.py:tool_execute`); handlers call `_resolve_user_id(instance)` which falls back to a `ChatSession` lookup for older rows.

## SSE Stream Layer

File: `app/routers/chat.py`

The graph runs under `astream_events(v2)`. Event translation:

| LangGraph event | SSE event | Notes |
|---|---|---|
| `on_chat_model_stream` | `response_chunk` | Token-by-token streaming of LLM output; accumulated into one persisted message |
| `on_custom_event name="widget"` | `widget` | Persisted as a `Message` with `message_type="widget"`, content = `instance_id`. Emitted by `tool_execute` (sub-agent widgets) or by `presenter` (main widget path). |
| `on_custom_event name="final_response"` | `response` | Sets `accumulated_content` from the glass text; flags `final_emitted=True` to avoid double-emit at the end |
| `on_tool_start` | `tool_start` + `thinking` | Drives the spinner and activity copy |
| `on_tool_end` | `tool_complete` | Preview for UI |
| interrupt detected post-stream | `interrupt` | Human-in-the-loop confirmation; graph resumes via `Command(resume=...)` on the next POST with `type="resume"` |
| end of stream | `response` + `done` | Final message persisted with `channel=req.channel`; knowledge sources appended as Sources block (chat only — suppressed in voice) |

## Request / Response Shape

Endpoint: `POST /api/chat/sessions/{session_id}/messages`

Request (`SendMessageRequest`):
- `content: str` — user message text
- `user_id: str`
- `type: "message" | "resume"` — resume path carries a `Command(resume=...)` into an interrupted graph
- `data: dict | None` — resume payload (e.g., `{widget_instance_id, confirmed}`)
- `channel: "chat" | "voice"` — pinned per session at the UI layer

Response: `text/event-stream` of JSON-encoded `StreamEvent`s (see the table above). The frontend reconstructs assistant messages from `response_chunk` / `response` events, renders widgets inline from `widget` events, and surfaces interrupts as confirmation cards.

## Channel Pinning

Channel is chosen per session. At the UI layer the toggle is locked as soon as the first message is sent (`messages.length > 0` in `frontend/src/components/chat/ChatThread.jsx`). When an existing session is selected, the store's channel is adopted from the first persisted message's `channel` column. New-chat restores the user's preferred default from `localStorage`.

Backend currently trusts the request's `channel` field. Defensive pinning at the DB level (e.g., a `channel` column on `ChatSession`) is not implemented — the UI lock is the single source of truth.

## Observability (LangSmith)

The only telemetry backend this service emits to is **LangSmith**. OpenTelemetry / APM bridges are hard-disabled at startup — see `app/observability.py:configure_langsmith()` which runs first in `app/main.py:lifespan` and sets `LANGSMITH_OTEL_ENABLED=false`, `LS_APM_OTEL_ENABLED=false`, and `OTEL_SDK_DISABLED=true`.

### Environment variables (`.env`)

| Var | Purpose | Default |
|---|---|---|
| `LANGSMITH_TRACING` | Master on/off. `false` → zero bytes leave the process (after the disable-OTel step). | `false` |
| `LANGSMITH_API_KEY` | Required when tracing is on. Missing key → auto-disables. | — |
| `LANGSMITH_ENDPOINT` | Company self-hosted URL, e.g. `https://langsmith.my-company.internal/api/v1`. Blank → LangSmith public cloud. | *(blank)* |
| `LANGSMITH_PROJECT` | Project name for grouping runs on the dashboard. | `finchat` |
| `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` | Redact payloads from spans before upload. Recommended for prod with user PII. | `false` |

`configure_langsmith()` sets both the canonical `LANGSMITH_*` names and the legacy `LANGCHAIN_*` aliases so any LangChain code path picks them up. It logs one line on boot: `[langsmith_configured] project=finchat endpoint=<url>` or `[langsmith_disabled] reason=<why>` — no secret is ever logged.

### Per-turn summary (`[turn_summary.v1]`)

One aggregated log line per user turn, emitted by `emit_turn_summary()`
in `app/agent/nodes.py` at stream end. Grep-friendly. Fields:

| Field | Meaning |
|---|---|
| `session`, `user`, `turn` | Keys |
| `exit` | `terminal` (widget/glass/final) or `text` (prose) or `error:...` |
| `total_ms` | Wall-clock duration of the turn |
| `iterations`, `llm_calls`, `llm_ms` | Planner LLM activity |
| `tool_calls`, `tool_ms` | Tool execution totals |
| `enrich_ms`, `presenter_ms` | Per-node latency contributions |
| `rehydrated` | `True` when `enrich()` reloaded profile/transactions after a process restart |
| `response_shape` | `prose_plus_widget` \| `widget_only` \| `no_widget`. Derived from `widget_emitted` + `prose_emitted` flags tracked across the turn. This is the headline observable for the compound-response work. |
| `planner_prompt_revision` | The value of `settings.planner_prompt_revision` when the turn ran. Lets rollback analysis compare shape distributions across revisions. |
| `hop_guard_triggered` | `True` when the two-phase cap fired. Investigate turns where this is True — either tune the prompt (stall) or relax the cap (legitimate turn 3). |
| `turn2_tool_delta`, `turn2_tool_repeat` | Only emitted on multi-phase turns (2+ Planner LLM calls). `delta` = tools unique to turn 2 (what the loop-back advanced to); `repeat` = boolean, true if turn 2 re-called any turn-1 tool. Absent on single-turn shapes — dashboards filter on presence. |
| `tools` | All tools called this turn (all phases concatenated in submission order) |

**Grep examples:**

```
# What's the compound-response rate?
grep '[turn_summary.v1]' app.log | grep -o 'response_shape=\w*' | sort | uniq -c

# How often does the hop guard fire, and with what intent?
grep '[hop_guard_triggered]' app.log

# Compound turns — what new tools did turn 2 reach for?
grep 'response_shape=prose_plus_widget' app.log | grep -o 'turn2_tool_delta=\S*'
```

### What gets traced

Every LangGraph invocation runs under a `RunnableConfig` carrying `run_name`, `tags`, and `metadata`. Built by `app.observability.trace_config(...)`:

| Graph | `run_name` | Tags | Metadata |
|---|---|---|---|
| Main orchestrator (`app/routers/chat.py`) | `chat.message` / `chat.resume` | `<channel>`, `user:<id>` | `user_id`, `session_id`, `channel`, `type` |
| Transfer sub-agent (`app/tools/transfer_tool.py`) | `transfer_money.<channel>` | `<channel>`, `agent:transfer_money`, `user:<id>` | `agent_name`, `channel`, `user_id`, `session_id` |
| Refund sub-agent (`app/tools/refund_tool.py`) | `refund_fee.<channel>` | `<channel>`, `agent:refund_fee`, `user:<id>` | same shape |

On the LangSmith dashboard each user turn appears as one root run (e.g. `chat.message`), with child spans per graph node (`planner_llm`, `tool_execute`, `presenter`, and — when a sub-agent fires — the full `transfer_money.chat` subtree with every `parse_node` / `tool_call_node` / `response_node`). Filter by any tag or metadata key. Within a session, all turns share the same `thread_id` so the checkpointer state is grouped alongside the spans.

**Sub-agent internal LLM calls** (parse-node extraction, llm_node bindings) are additionally tagged `subagent_internal` via `RunnableConfig.tags`. The chat SSE stream in `app/routers/chat.py` uses that tag to filter out `on_chat_model_stream` events so the raw JSON doesn't leak to the user — the tag is a dual-purpose signal (SSE filter + LangSmith grouping).

### Why only LangSmith

The user-facing spec for this app requires **one** observability destination. Adding a parallel OTel → Jaeger/Datadog/etc. export would double-send traces and double the privacy surface. The `configure_langsmith()` call sets `OTEL_SDK_DISABLED=true` unconditionally so that even if a future dependency ships an OTel auto-instrumentor, it stays inert until someone deliberately removes that line.

### Turning on for a company self-hosted instance

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<issued by your LangSmith admin>
LANGSMITH_ENDPOINT=https://langsmith.my-company.internal/api/v1
LANGSMITH_PROJECT=finchat-staging
# Redact for any environment that sees real user data:
LANGSMITH_HIDE_INPUTS=true
LANGSMITH_HIDE_OUTPUTS=true
```

If the self-hosted instance uses a self-signed cert, set `REQUESTS_CA_BUNDLE=/path/to/ca.pem` at the OS level. It's not a LangSmith-specific flag so it lives outside `Settings`.

## Persistence

- **SQLite** (`Message`, `ChatSession`, `MemoryFact`, `WidgetInstance`, `SubAgentTemplate`): transactional state and history. `Message.channel` records the channel each message was produced in. `SubAgentTemplate` rows carry `source` (`"seed"` for file-seeded regulated flows, `"user"` for builder-authored) and `status` (`draft`/`deployed`/`disabled`). The legacy `AgentDefinition` table is no longer used by the runtime; it persists only as an unused leftover.
- **ChromaDB**: long-term memory facts and document embeddings for RAG.
- **LangGraph checkpointer**: in-graph state (`AgentState`) keyed by `thread_id = session_id`. Enables interrupts and the resume flow.
- **Per-process memory** (`app/agents/runtime.py:_INNER_STATE`): accumulated sub-agent inner state across outer interrupts. Wiped on backend restart; an acceptable Phase-1 limitation for voice dialogues.
- **Seed data files** (`api_data/transfer/<login_id>/*.json`, `api_data/refund/<login_id>/*.json`): mock bank-API responses. Present for `alexm` and `aryash`; absent for `chrisp` (exercises the ineligible path).
- **Flat file** (`data/kb_descriptor.txt`): a single-line summary of what's in the knowledge base, regenerated on upload/delete and read by `knowledge_search.description()` on every turn.

## File Map

### Main orchestrator

| Concern | File |
|---|---|
| App lifespan + tool/agent init + KB descriptor bootstrap + template seeding | `app/main.py` |
| Graph wiring | `app/agent/graph.py` |
| Planner + routers (`should_route`, `post_tool_router`) + `tool_execute` + widget persistence | `app/agent/nodes.py` |
| Presenter (deterministic rules engine) | `app/agent/presenter.py` |
| State schema (`AgentState`) | `app/agent/state.py` |
| Checkpointer | `app/agent/checkpointer.py` |

### Tools

| Concern | File |
|---|---|
| `BaseTool` / `ToolResult` | `app/tools/base.py` |
| Tool registry + filters + `init_tools()` | `app/tools/__init__.py` |
| `AgentTool` base + `@action` decorator + `AGENT_TOOL_REGISTRY` | `app/tools/agent_tool.py` |
| Data tools (`get_profile_data`, `get_accounts_data`, `get_transactions_data`) | `app/tools/data_tools.py` |
| `present_widget` handoff sentinel | `app/tools/handoff.py` |
| `knowledge_search` tool | `app/tools/knowledge_search.py` |
| `tool_search` tool | `app/tools/tool_search.py` |
| Transfer Planner entry (`TransferAgentTool`) + legacy `transfer_actions` shim | `app/tools/transfer_tool.py`, `app/tools/transfer_actions.py` |
| Transfer unified AgentTool (6 actions on `TransferService`) | `app/tools/transfer_ops.py` |
| Refund Planner entry (`RefundAgentTool`) | `app/tools/refund_tool.py` |
| Refund unified AgentTool (3 actions on `RefundService`) | `app/tools/refund_ops.py` |

### Sub-agent framework (v4)

| Concern | File |
|---|---|
| Sub-agent registry + `template_for_agent(agent_name, channel)` | `app/agents/__init__.py` |
| `SubAgentState` | `app/agents/state.py` |
| Node factories (`parse_node`, `condition_node`, `tool_call_node`, `interrupt_node`, `llm_node`, `tool_node`, `response_node`) | `app/agents/nodes/` |
| Predicate DSL compiler (pure, no `eval`) | `app/agents/predicates.py` |
| Template → `StateGraph` compiler (runtime-injected escape / retry edges) | `app/agents/template_compiler.py` |
| Template validator + `LoadedTemplate` dataclass | `app/agents/template_loader.py` |
| DB-backed template store + seed-from-files | `app/agents/template_store.py` |
| File-based discovery helpers + `initialize_templates()` | `app/agents/templates/__init__.py` |
| Regulated seed templates | `app/agents/templates/*.json` |
| Pattern library (builder skeletons) | `app/agents/patterns/` |
| Per-process driver runtime (thread registry, accumulated inner state) | `app/agents/runtime.py` |
| Escape classifier (abort / topic-change) | `app/agents/escape.py` |
| Regex parsers (`money`, `yes_no`, `account_keyword`, `last4`) + LLM structured-output helper | `app/agents/parsers/` |

### Widgets

| Concern | File |
|---|---|
| Widget catalog (single source of truth) — `render_fn`, `slot_arg_map`, rules metadata, version hash | `app/widgets/catalog.py` |
| Widget builder functions (pure; called by Presenter + sub-agent response_nodes) | `app/widgets/builders.py` |
| Widget summarizers (voice) | `app/widgets/summarizers.py` |
| Widget-action handlers (`transfer_form` + `refund_form` 3-stage state machines) | `app/widgets/actions.py` |
| `WidgetInstance` SQL model | `app/models/widget_instance.py` |
| `SubAgentTemplate` SQL model | `app/models/sub_agent_template.py` |

### Services

| Concern | File |
|---|---|
| LLM provider + variants registry (reasoning-model handling) | `app/services/llm_service.py` |
| RAG + KB descriptor read/write | `app/services/rag_service.py` |
| Indexing + KB descriptor refresh hook | `app/services/indexing_service.py` |
| Base system prompt assembly | `app/services/enrichment.py` |
| Memory | `app/services/memory.py` |
| Profile service (in-memory, file-seeded at login) | `app/services/profile_service.py` |
| Transfer service (mock API) + loader | `app/services/transfer_service.py`, `app/services/transfer_data_loader.py` |
| Refund service (mock API) + loader | `app/services/refund_service.py`, `app/services/refund_data_loader.py` |

### Routers

| Concern | File |
|---|---|
| SSE chat stream (filters `subagent_internal` events) | `app/routers/chat.py` |
| Agents API (`/api/agents` — templates CRUD + patterns + deploy/disable) | `app/routers/agents.py` |
| Tools API (`/api/tools?agent_name=<name>` returns actions metadata) | `app/routers/tools.py` |
| Widgets API (`/api/widgets/{id}/action` runs widget-action handlers) | `app/routers/widgets.py` |
| Auth + profile inspection (`/api/profiles/{id}/full`) | `app/routers/auth.py` |

### Docs

| Doc | Contents |
|---|---|
| `backend/docs/architecture.md` | This file — orchestrator + sub-agent framework overview |
| `backend/docs/widgets.md` | Widget catalog contract + Presenter's 4-rule engine |
| `backend/docs/transfer_flow.md` | End-to-end Transfer walkthrough (actors, components, chat flow, voice flow, AgentTool, governance) |
| `backend/docs/get_profile_data.md` | `get_profile_data` tool reference with explicit gap analysis |
| `backend/docs/sub_agents.md` | Sub-agent framework deep dive |
| `backend/docs/compound_response_plan.md` | Three-shape response strategy design — fast-path / two-phase / no-widget, hop-guard cap, eval + observability plan |

### Scripts

| Concern | File |
|---|---|
| Planner-routing eval harness (score `response_shape` + tool choice against labels) | `scripts/eval_planner_routing.py`, `scripts/eval_rows.jsonl` |

### Tests

| Concern | File |
|---|---|
| P0.2 present_widget mechanics, post_tool_router branches, hop-guard, error-path, streaming-order integration | `tests/test_handoff_and_router.py`, `tests/test_streaming_order.py` |
| Presenter rules engine (4 rules, catalog round-trip) | `tests/test_presenter_selector.py`, `tests/test_presenter_catalog_roundtrip.py` |
| Frontend E2E — prose-above-widget DOM order for compound messages (Playwright scaffold; requires `npm install --save-dev @playwright/test && npx playwright install`) | `frontend/tests/compound_response.spec.js`, `frontend/playwright.config.js` |

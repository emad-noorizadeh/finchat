# Building a Sub-Agent — 10-Minute Overview

A high-level deck. ~10 slides, ~1 minute each. No live coding — just enough for the audience to know what a sub-agent is, why it's shaped this way, and where to look next.

---

## Slide 1 — Title (30 sec)

**Building a Sub-Agent**
*Templates, not classes. Predicates, not LLMs.*

Speaker notes:
- Goal of this talk: in 10 minutes, give you the mental model. Not a how-to — a why.
- If you want the hands-on tutorial, see the longer deck.

---

## Slide 2 — What problem are we solving? (1 min)

The old way: an LLM picks the next step at every turn.

- Malformed tool payloads.
- Skipped confirmations.
- Dead loops.

Even when the underlying tool was safe, the *experience* was unsafe.

**We needed deterministic procedures the LLM can call, not LLM-driven procedures.**

---

## Slide 3 — The core idea (1 min)

A sub-agent is a **JSON template** that compiles into a **graph**.

- The main orchestrator calls it like a tool.
- Inside, the graph runs deterministically — predicates decide routing, not LLMs.
- LLMs are used for *content* (parsing fuzzy input, drafting prose), never for *orchestration*.

> Tools own the rules. Sub-agents own the experience.

---

## Slide 4 — Four layers of responsibility (1 min)

| Layer | Owns |
|---|---|
| **Tool** | Business rules, auth, limits, fraud. |
| **Template** | The procedure shape — nodes + edges. |
| **Sub-agent runtime** | Drives the compiled graph step by step. |
| **Orchestrator** | Routes user intent, paraphrases results. |

Speaker notes:
- Clean separation means each layer can be changed without breaking the others.
- Regulated flows benefit most — audit boundary is crisp.

---

## Slide 5 — The graph, in one picture (1 min)

```
User ──► Orchestrator (Planner)
            │ calls e.g. transfer_money or card_advisor
            ▼
        Sub-agent tool
            │ loads template, compiles to a StateGraph (cached)
            ▼
        Inner graph
            entry → parse → dispatch → tool_call → … → response
            │
            │ pauses at interrupt_node for user input
            ▼
        Result back to Planner — widget, glass, or text
```

Speaker notes:
- Compilation happens once per template, then cached.
- Pause/resume is handled outside the inner graph by the driver.

---

## Slide 6 — The node toolbox (1 min)

Eight node types — that's the whole vocabulary.

| Node | Role |
|---|---|
| `parse_node` | Pull values out of the user's message. |
| `condition_node` | Route based on state. **Predicates, not LLMs.** |
| `interrupt_node` | Pause and ask the user a question. |
| `tool_call_node` | Call a tool with hard-coded params. |
| `parallel_tools_node` | Several tool calls at once, one merged result write. |
| `llm_node` + `tool_node` | Let the LLM pick from a tool subset. |
| `response_node` | Finish — return a widget, text, or hand back to the orchestrator. |

Most sub-agents are: `parse → dispatch → (tool_call / interrupt)+ → response`.

---

## Slide 7 — The most important slide: two registries (1.5 min)

```
Tool registry             ← what the LLM Planner sees
  (BaseTool subclasses, registered via register_tool)

Agent registry            ← metadata about templates that exist
  ("agent X has chat + voice variants")
```

**A template alone is NOT callable.**

To make a template reachable from the LLM, *something* has to register a tool whose `execute()` runs the template's graph.

- **Non-regulated:** `DynamicSubAgentTool` auto-creates it. You do nothing.
- **Regulated:** you hand-write a `BaseTool` subclass. Without it → invisible to the Planner.

---

## Slide 8 — Two paths to ship one (1 min)

| Path | Use when |
|---|---|
| **Agent Builder UI** | Non-regulated. Iterate live. No restart. |
| **Seed JSON file** in `app/agents/templates/` | Bundled with the image. Used for regulated agents (paired with a hand-coded tool) and starter templates. |

Both paths end up as a row in the `sub_agent_templates` table — that's the runtime source of truth.

After bootstrap, the DB always wins. To redeploy a seeded template, use the admin import endpoint.

---

## Slide 9 — What's deterministic, what's not (1 min)

| Deterministic (no LLM) | Fuzzy (LLM) |
|---|---|
| Routing between nodes | Parsing user replies into slots* |
| Tool dispatch + params | Drafting prose for the user |
| Slot reads/writes | Recommending an answer from context |
| Pause/resume mechanics | Picking which tools to call inside an `llm_node` |
| Escape classifier (cancel / topic-change) | — |

\* Entry parsing is often free now: templates can declare **parameters** the
orchestrator LLM fills when it invokes the agent — those seed the slots, and
the parse LLM call is skipped or narrowed to what's still missing. Interrupt
replies always get a full parse. See the full deck, Slide 6b.

The audit boundary lives at the deterministic layer. Every regulated decision is explainable from state + predicates.

---

## Slide 10 — Key takeaway + where to read more (1 min)

> **A template defines a graph. A tool makes it callable.**
> Don't ship one without the other.

If you remember nothing else: the agent registry and the tool registry are *different things*, and the LLM only ever sees the tool registry.

**Where to go next:**

- `backend/docs/sub_agents.md` — overview + locked principles.
- `backend/docs/extending_tools_and_agents.md` — full authoring guide.
- `tutorial_build_a_subagent.md` — hands-on walkthrough (build `card_offer` end-to-end).
- Patterns to clone from: `app/agents/patterns/collect_one_slot.json`, `confirm_then_execute.json`.

Q&A.

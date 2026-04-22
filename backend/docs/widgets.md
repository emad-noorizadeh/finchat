# Widgets — authoring & rendering contract

Every widget in FinChat flows through one path:

1. A **data tool** populates `state.variables[<slot>]`.
2. The Planner LLM decides to render and calls `present_widget()`.
3. The **Presenter** (`app/agent/presenter.py`) is a **deterministic rules engine** that inspects `state.variables` and picks one render builder.
4. The builder produces a widget dict; the Presenter persists + dispatches it; the graph ends.

No LLM is involved in widget selection. Rules are evaluated in order and the first match wins.

## The four rules

### Rule 1 — Designed composite (exact set match)

If the populated-slots set exactly equals some catalog entry's `slot_combination`, that entry's `render_fn` fires.

- `populated == set(slot_combination)` — no subset, no superset
- Tiebreaker (multiple composites with identical combos): **catalog declaration order**

### Rule 2 — Single mapped slot

If exactly one populated slot maps to a catalog entry via `default_data_var`, that widget fires.

### Rule 3 — Generic composite

If 2+ populated slots map to composable widgets (`composable != "never"`), render `generic_composite`.

- Sections ordered by `composite_priority` (lower first), tiebroken by slot population order within the turn.
- Capped at 3 sections; overflow logged as `[presenter_truncate]`.
- Title: the Planner's most recent `AIMessage.content` if non-empty; otherwise empty string (sections self-label).

### Rule 4 — Text-card fallback

Everything else. Emits a `text_card` with:

1. The Planner's most recent prose if available; otherwise
2. Per-slot summaries from `widget_to_llm()` joined by newlines; otherwise
3. Literal "I didn't find anything to show."

## Adding a new widget

1. **Write a builder** in `app/widgets/builders.py`. Signature: take explicit kwargs, return a JSON-string widget envelope. Keep it pure — no catalog access, no state access.

2. **Add a catalog entry** in `app/widgets/catalog.py`:

   ```python
   "my_widget": {
       "display_name": "My Widget",
       "description": "What it shows.",
       "tier": 1,                                   # 1 = designed, 2 = generic fallback
       "composable": "full",                        # "full" | "degraded" | "never"
       "fields": [{"name": ..., "type": ...}, ...], # metadata for the /widgets page
       "sample_data": {...},                        # frontend preview (widget.data shape)
       "standalone_render": "MyWidget",             # React component name

       # Rendering contract — required for Presenter dispatch:
       "render_fn": my_widget_widget,               # callable, NOT a name string
       "default_data_var": "my_slot_data",          # (for Rule 2) OR
       "slot_combination": ["slot_a", "slot_b"],    # (for Rule 1, designed composite)
       "slot_arg_map": {"my_slot_data": "payload"}, # REQUIRED — maps slot → builder kwarg
       "sample_build_args": {"payload": {...}},     # REQUIRED — kwargs for test + validation

       # Optional:
       "composite_priority": 50,                    # Rule 3 ordering; lower = earlier
       "voice_summary_template": "...",             # TTS-friendly summary
   }
   ```

3. **Add a React component** in `frontend/src/components/widgets/` and register it in `WIDGET_MAP` (`WidgetRenderer.jsx`).

4. **Restart backend**. `_validate_catalog()` runs at module load and raises on:
   - missing `slot_arg_map` when `default_data_var` or `slot_combination` is set
   - `slot_arg_map` keys that don't cover `slot_combination`
   - `render_fn(**sample_build_args)` that throws `TypeError` (signature drift)
   - `default_data_var` collisions across entries

## Contracts

| Component | Rule |
|---|---|
| Builder | Pure function. `(kwargs) → JSON string`. No catalog, no state. |
| `slot_arg_map` | Required when entry has `default_data_var` or `slot_combination`. No identity fallback. |
| `render_fn` | Callable reference (not a tool name). Lives in `app/widgets/builders.py`. |
| `sample_build_args` | Explicit test fixture — not synthesized. Must match builder signature. |
| `slot_combination` | Set of slot names. Order irrelevant for matching. |
| `default_data_var` | Must be unique across the catalog. |

## Observability

The Presenter emits one log line per decision:

```
[presenter_choice] rule=<rule> widget=<widget_type> slots=[...]
[presenter_truncate] rule=generic_composite total=N keeping=3 dropped=[...]
[presenter_title_source] turn_distance=N title_len=N
[presenter_designed_composite_missed] populated=[...] closest=<widget_type> extras=[...]
[presenter_error] rule=... widget=... error=... — falling back to text_card
```

`[presenter_designed_composite_missed]` fires when a designed composite's `slot_combination` was a subset of populated slots by exactly one extra slot. High frequency → consider a larger designed composite OR subset-match rule 1.5.

## Non-goals / explicit limits

- **LLM Presenter**: removed in v8. Every decision is rule-driven. If a genuinely ambiguous case emerges, add it as rule 1.5 with a specific classifier — not a full LLM.
- **4+ section composites**: truncated at 3 for UI readability. Raise with evidence if `[presenter_truncate]` is frequent.
- **Subset match for designed composites**: exact match only. Over-fetching falls through to Rule 3 (generic composite) — no designed composite is dropped silently.
- **Builder → catalog coupling**: forbidden. Builders never read `WIDGET_CATALOG`. Composite section rendering is the frontend's job.

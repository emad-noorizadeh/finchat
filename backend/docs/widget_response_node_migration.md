# Widget response_node migration (Position 2)

Migrate sub-agent `response_node(return_mode=widget)` from hand-rolled
`data_template` dicts to direct calls into `WIDGET_CATALOG[*].render_fn`.
Eliminates the dual-path widget pipeline (Presenter vs. sub-agent) and makes
the builder signature the single source of widget shape.

## 1. Why

Today, `response_node` widget mode is just routing — it tells the runtime
"emit this dict to the renderer instead of paraphrasing." The actual shape
of the dict is whatever the template author typed under `data_template`.
The catalog `fields` list is documentation, not validation. The builder
functions in `app/widgets/builders.py` are bypassed entirely on this path.

Consequences in production today:
- `transfer_money.chat.json` ships `from_account_hint`, `to_account_hint`,
  `transfer_type`, `notice` — none of which `transfer_form_widget` accepts.
  The frontend `TransferForm.jsx` reads them anyway. Drift is silent.
- `refund_fee.chat.json` ships `fee_type_hint` and `amount_hint` — neither
  read by `RefundForm.jsx`. Pure dead writes.
- A future widget change must be coordinated across catalog, builder,
  every template that uses it, and the React component. Four-way drift is
  the steady state.

After the migration:
- One pipeline. `response_node` calls `render_fn(**kwargs)` the same way
  the Presenter does.
- The schema chain is explicit and unified (see §1.1).
- Templates declare an explicit `kwargs` map from state paths to builder
  kwargs — no shape responsibility on the author.
- If required kwargs are missing at runtime, response_node can gracefully
  fall back to a text response (see §4.3.1) rather than render a broken
  widget.

## 1.1 Schema ownership chain

The widget contract lives in three places, in this order:

1. **The React component** (`frontend/src/components/widgets/X.jsx`) is
   the consumer-side schema — it defines what the rendering surface can
   actually display. Whatever shape it reads off `widget.data` is the
   ground truth.
2. **The catalog entry** (`WIDGET_CATALOG[x]`) propagates that schema:
   `fields[]` declares names + types + required-ness; `standalone_render`
   names the React component; `render_fn` points to the builder.
3. **The builder function** (`builders.py:X_widget(...)`) is the
   producer-side guarantee that what Python emits matches what the
   React component reads.

Today these three are out of sync (see §2). After the migration, the
catalog is the single source of truth: any field change starts at the
React component, is reflected in the catalog `fields`, and forces a
matching change in the builder signature. `response_node` and the
Presenter both go through the builder, so authors and the runtime see
the same contract.

**Authoring corollary.** The agent designer is responsible for ensuring
that the upstream pipeline (parse_node, tool_call_node, llm_node,
interrupt_node) writes enough into `state.variables` to populate every
field the chosen widget declares as required. The Agent Builder makes
this visible — see §4.5.1.

## 2. Triple-source reconciliation (as of writing)

| widget_type | Used by sub-agent? | Builder kwargs | data_template keys | Frontend reads | Drift to fix |
|---|---|---|---|---|---|
| profile_card | no | `profile_data, title` | — | `name, city, state, rewards_tier, segment, credit_scores, qualifying_balance` | none |
| account_summary | no | `accounts, title` | — | `accounts` | none |
| transaction_list | no | `payload, page, page_size, title` | — | `shape, transactions, groups, applied_filters, group_by` | none |
| transfer_confirmation | no | `from_account, to_account, amount, date, confirmation_id, status` | — | `from, to, amount, date, confirmation_id, status` | none |
| confirmation_request | no | `title, details, fields` | — | `details, fields` | none |
| text_card | no | `content, title` | — | `content` | none |
| profile_with_accounts | no | `profile, accounts, title` | — | `profile, accounts` | none |
| generic_composite | no | `sections, title` | — | `sections` | none |
| **transfer_form** | **yes** | `amount, from_account, to_account, source_options, target_options, validation_id, title` | `amount, from_account, to_account, from_account_hint, to_account_hint, source_options, target_options, transfer_type, payee_hint, notice` | `+from_account_hint, to_account_hint, transfer_type, notice, validation_result, _stage, confirmation_id, effective_date, submit_error` | add `from_account_hint, to_account_hint, transfer_type, notice` to builder; drop `payee_hint` |
| **refund_form** | **yes** | `account_details, refundable_transactions, total_amount, title` | `+ fee_type_hint, amount_hint` | `account_details, refundable_transactions, total_amount, decision, selected_activity_reference, _stage, submit_error` | drop `fee_type_hint, amount_hint` |

Only two widgets are currently emitted by sub-agent `response_node`:
`transfer_form` and `refund_form`. The other eight catalog entries are
Presenter-only, so the migration is a no-op for them — they already go
through the builder.

### Action-handler-populated keys (`runtime_fields`)

`TransferForm.jsx` reads `_stage`, `validation_result`, `confirmation_id`,
`effective_date`, `submit_error`. `RefundForm.jsx` reads `_stage`,
`decision`, `selected_activity_reference`, `submit_error`. None of these
appear in the initial render data — they're merged in by the widget action
handler post-submit (see `app/widgets/actions.py`). The builder owns
initial state only; action handlers own runtime mutations. Migration must
not break this seam.

To make this contract visible, the catalog grows a new optional field
**`runtime_fields`** alongside `fields`: a documentation-only list of
keys that the action-handler injects after the initial render. Builders
do not produce these; the kwargs editor in the Agent Builder doesn't
surface them as configurable; the loader doesn't require them. They
exist so the full widget contract — initial render shape (`fields`) +
runtime mutation shape (`runtime_fields`) — is one place in the catalog.

### `validation_id` vs `validation_result` (related but distinct)

Two similar names that mean different things:

- **`validation_id`** — a short string handle returned by the transfer
  tool's `validate` action and passed back into the `submit` action.
  Carried as a builder kwarg on `transfer_form_widget` so the React
  component can echo it back when the user clicks Submit. Scalar.
- **`validation_result`** — a full payload dict (`status`,
  `error_category`, etc.). Two places it can appear:
  1. In `state.variables.validation_result` when the validate
     `tool_call_node` writes its output_var (see
     `transfer_money.voice.json`'s flow — used by predicates to branch
     on error vs. confirm).
  2. In the widget's `data.validation_result` after the widget-action
     handler stages it post-validate (`app/widgets/actions.py:112`).

They aren't redundant; they describe different rungs of the same flow.
The kwargs editor surfaces `validation_id` (the handle); `validation_result`
is either an upstream state slot or a runtime_field, depending on context.

### Voice variants — out of scope

`transfer_money.voice.json` and `refund_fee.voice.json` exist but do not
use `return_mode == "widget"`. They emit via `to_orchestrator` /
`glass_template`. This migration touches chat variants only.

## 3. Target shape

```json
{
  "id": "response_form_m2m",
  "type": "response_node",
  "data": {
    "label": "Show m2m form",
    "return_mode": "widget",
    "widget": {
      "widget_type": "transfer_form",
      "kwargs": {
        "amount": "{{variables.amount}}",
        "from_account": "{{variables.from_account}}",
        "to_account": "{{variables.to_account}}",
        "from_account_hint": "{{variables.from_account_hint}}",
        "to_account_hint": "{{variables.to_account_hint}}",
        "source_options": "{{variables.transfer_details.sourceAccounts}}",
        "target_options": "{{variables.transfer_details.destinationAccounts}}",
        "transfer_type": "m2m",
        "title": "Confirm transfer"
      },
      "metadata": {"flow": "chat", "transfer_type": "m2m"}
    }
  }
}
```

Runtime resolves each value through `resolve_templates(...)`, then calls
`transfer_form_widget(**kwargs)`. `metadata` continues to ride alongside as
a free-form annotation (read by the action-handler dispatch layer; not a
builder concern).

## 4. Implementation steps

### 4.1 Builder signature changes

`backend/app/widgets/builders.py`:

```python
def transfer_form_widget(
    amount: float | None = None,
    from_account: dict | None = None,
    to_account: dict | None = None,
    source_options: list | None = None,
    target_options: list | None = None,
    validation_id: str | None = None,
    title: str = "Confirm transfer",
    # NEW kwargs from template drift:
    from_account_hint: str | None = None,
    to_account_hint: str | None = None,
    transfer_type: str | None = None,
    notice: str | None = None,
) -> str:
    return json.dumps({
        "widget": "transfer_form",
        "title": title,
        "icon": "send",
        "data": {
            "amount": amount,
            "from_account": from_account,
            "to_account": to_account,
            "source_options": source_options or [],
            "target_options": target_options or [],
            "validation_id": validation_id,
            "from_account_hint": from_account_hint,
            "to_account_hint": to_account_hint,
            "transfer_type": transfer_type,
            "notice": notice,
        },
        "actions": [...],
        "metadata": {"status": "pending"},
    })
```

`refund_form_widget` needs no signature change (the template's
`fee_type_hint`/`amount_hint` keys are dead — drop them in step 4.4).

**Type coercion.** `resolve_templates("{{X}}", state)` is a single-template
exact-match passthrough — it returns the raw value from state, preserving
its type (number stays number, dict stays dict). Mixed strings like
`"Hello {{name}}!"` always stringify. The builder can assume kwargs carry
their authored type. Where the upstream node may emit a string for a
numeric kwarg (e.g. a regex parser extracting `amount` as text), the
builder performs explicit coercion at the top of its body. Type validation
is otherwise advisory — the builder is permissive, the React component is
the strict consumer.

### 4.2 Catalog `fields` and `runtime_fields` updates

For each widget that grew kwargs, mirror the new fields in
`WIDGET_CATALOG[*]["fields"]` so the Agent Builder UI can surface them.
`sample_data` should also gain the new keys with realistic values so the
`/widgets` preview reflects production shape.

Add `runtime_fields` (new optional list) to each catalog entry that has
action-handler-injected keys:

```python
"transfer_form": {
    ...
    "fields": [...],
    "runtime_fields": [
        {"name": "_stage", "type": "string"},
        {"name": "validation_result", "type": "object"},
        {"name": "confirmation_id", "type": "string"},
        {"name": "effective_date", "type": "string"},
        {"name": "submit_error", "type": "string"},
    ],
    ...
},
"refund_form": {
    ...
    "fields": [...],
    "runtime_fields": [
        {"name": "_stage", "type": "string"},
        {"name": "decision", "type": "object"},
        {"name": "selected_activity_reference", "type": "string"},
        {"name": "submit_error", "type": "string"},
    ],
    ...
},
```

Widgets without an action handler (`profile_card`, `text_card`, etc.)
either omit `runtime_fields` or set it to `[]`.

`_validate_catalog()` round-trip test catches signature mismatches once
`sample_build_args` is updated to pass any required new kwargs.
`runtime_fields` does not participate in the round-trip check — it's
documentation, not contract.

### 4.3 `response_node.py` runtime change

**Feature flag.** The new branch is gated by
`settings.feature_response_node_builder` (default `True`, overridable via
the `FEATURE_RESPONSE_NODE_BUILDER` env var). When set to `False`, every
response_node falls back to the legacy `data_template` path regardless of
config. Rollback path without redeploy.

**`resolve_templates` semantics (load-bearing).** The kwargs editor and
fallback check both depend on a specific contract:

- `resolve_templates("{{variables.X}}", state)` — single-template exact
  match — returns the raw value from state (preserves type), or `None`
  if X is missing.
- `resolve_templates("Hello {{variables.X}}", state)` — mixed content —
  always returns a string. Missing values render as `""`.
- `resolve_templates({"a": "{{variables.X}}"}, state)` — dict walk —
  recurses into each value, applying the same rules.

Authored kwargs are almost always single-template form
(`"{{variables.amount}}"`), so the resolved value retains its native
type and a missing slot resolves to `None`. The `_empty()` check in
§4.3.1 leans on `None` being the missing-value sentinel for this path.
No pre-migration fix needed; the contract is already implemented this
way in `app/utils/templates.py`.

**Legacy vs. new path precedence.** Three cases on `widget_cfg`:

| `kwargs` | `data_template` | Path taken |
|---|---|---|
| absent or `None` | present | Legacy (hand-rolled dict, deprecation warning) |
| absent or `None` | absent | Error (nothing to render) |
| `{}` empty dict | any | **New path** — required-kwarg check fires, almost certainly triggering error/fallback |
| populated dict | any | New path |

An author opting in to the new path with an empty `kwargs: {}` is
explicitly asserting "I want the new validation," even if the dict is
empty — they'll hit the §4.3.1 missing-required path immediately, which
is the right failure mode.

**`actions` override.** Templates **cannot** override the builder's
`actions` array on the new path. Actions are part of the widget's
interactive contract — action `id`s map to click-handler dispatch
(`onTransferSubmit`, `onRefundSubmit`) that the React component and
the backend widget-action endpoint both depend on. Allowing template
override would silently break the dispatch seam. Contrast with
`metadata`, which is free-form annotation and is merge-overridable.

**Asymmetry note.** The legacy path continues to honor template-supplied
`actions` (`widget_cfg.get("actions") or []`). This is preserved only
for backward compatibility during the dual-path window — it lets an
unmigrated template keep working even if it ships custom actions. The
asymmetry disappears with step 6 of §5 (legacy path removal). The
template scan in §4.4 confirms no production template actually uses
this seam today.

Replace lines 69–76 in `backend/app/agents/nodes/response_node.py`:

```python
if return_mode == "widget":
    from app.config import settings
    from app.widgets.catalog import WIDGET_CATALOG

    widget_type = widget_cfg.get("widget_type") or ""
    entry = WIDGET_CATALOG.get(widget_type)

    if entry is None or not callable(entry.get("render_fn")):
        raise ValueError(
            f"response_node {widget_type!r}: unknown widget_type or no render_fn in catalog"
        )

    raw_kwargs = widget_cfg.get("kwargs")
    has_kwargs = raw_kwargs is not None  # `{}` opts in; absent/None opts out
    if has_kwargs and not isinstance(raw_kwargs, dict):
        raise ValueError(
            f"response_node {widget_type!r}: widget.kwargs must be a dict, got {type(raw_kwargs).__name__}"
        )

    take_new_path = (
        has_kwargs
        and settings.feature_response_node_builder
    )

    if not take_new_path:
        # Legacy fallback (dual-path during migration, or feature flag
        # off). Authors who still use `data_template` get the old
        # behavior with a deprecation log.
        if widget_cfg.get("data_template") is None:
            raise ValueError(
                f"response_node {widget_type!r}: neither widget.kwargs nor "
                f"widget.data_template provided"
            )
        logger.warning(
            "[response_node_widget_legacy] node=%s widget=%s: using hand-rolled "
            "data_template — migrate to widget.kwargs (see widget_response_node_migration.md)",
            data.get("label") or "?", widget_type,
        )
        widget = {
            "widget": widget_type,
            "title": resolve_templates(widget_cfg.get("title") or "", state),
            "data": resolve_templates(widget_cfg.get("data_template") or {}, state),
            "actions": widget_cfg.get("actions") or [],
            "metadata": widget_cfg.get("metadata") or {},
        }
    else:
        # New path. Resolve every kwarg through the shared template
        # engine; single-template strings return raw values (None if
        # missing), mixed strings stringify (empty if missing).
        resolved_kwargs = {k: resolve_templates(v, state) for k, v in raw_kwargs.items()}

        # Required-kwarg check (§4.3.1) — must precede the builder call
        # since the builder accepts None defaults and would otherwise
        # silently render a broken widget.
        required_field_names = {
            f["name"] for f in (entry.get("fields") or [])
            if f.get("required") is True
        }
        def _empty(v):
    if isinstance(v, str): return not v.strip()
    return v is None or v == [] or v == {}
        missing = sorted(
            name for name in required_field_names
            if _empty(resolved_kwargs.get(name))
        )
        if missing:
            # See §4.3.1 for fallback-text handling. Default mode is "error".
            ...  # full code in §4.3.1

        try:
            widget = json.loads(entry["render_fn"](**resolved_kwargs))
        except TypeError as e:
            raise ValueError(
                f"response_node {widget_type!r}: builder rejected kwargs ({sorted(resolved_kwargs)}): {e}"
            )
        # Templates may merge into `metadata` (annotation only). They may
        # NOT override `actions` — those are part of the interactive
        # contract owned by the builder + React component + action handler.
        if widget_cfg.get("metadata"):
            widget = {**widget, "metadata": {**(widget.get("metadata") or {}), **widget_cfg["metadata"]}}
        if widget_cfg.get("actions"):
            logger.warning(
                "[response_node_widget_actions_ignored] node=%s widget=%s: "
                "template tried to override builder actions; ignoring (actions "
                "are part of the dispatch contract).",
                data.get("label") or "?", widget_type,
            )

    variables = dict(state.get("variables") or {})
    variables["_response_widget"] = widget
    variables["_return_mode"] = "widget"
    result["variables"] = variables
```

The new path:
1. Looks up the catalog entry.
2. Resolves each kwarg value through `resolve_templates` so authors keep
   `{{variables.X}}` substitution.
3. Calls `render_fn(**kwargs)` and parses the JSON string the builder
   returns (builder API is unchanged — returns a JSON-encoded dict).
4. Merges template-supplied `metadata` overrides onto the builder default.

Legacy `data_template` path is preserved with a deprecation warning so we
can migrate templates one at a time without coordinating a flag day.

### 4.3.1 Fallback to text when required data is missing

The builder accepts `None` defaults for every kwarg, so a missing slot
will silently render an empty widget if we don't check. Catalog `fields`
already declare which fields are `required: True`; we use that.

New response_node config:

```jsonc
{
  "type": "response_node",
  "data": {
    "return_mode": "widget",
    "widget": {
      "widget_type": "transfer_form",
      "kwargs": { ... },
      "on_missing_required": "fallback_text",
      "fallback_text": "I couldn't put together your transfer form — could you share the amount and accounts again?"
    }
  }
}
```

The fallback text is a regular template string; `{{variables.X}}` works
inside it. Authors must reference slots that actually exist in their
graph — there is no general "last tool error" convention in
`SubAgentState`; the agent designer is responsible for writing whatever
context they want the fallback to surface.

`on_missing_required` is one of:

- **`"error"`** (default) — raise `ValueError` if any catalog-required
  field resolves to `None` / `""` / `[]` / `{}`. Stops the graph with a
  clear message. Right default for regulated templates where a broken
  widget is worse than a halted turn.
- **`"fallback_text"`** — emit a `to_orchestrator`-style text response
  instead of the widget. The graph still terminates, but the user sees
  prose, not a half-rendered card. Right default for free-form
  conversational sub-agents.

**When this check runs.** Only on the **new path** (§4.3) — after
`resolved_kwargs` is built and before the builder call. The legacy
`data_template` branch is exempt: it has no catalog-`required` semantics
and would false-positive on every field. The flag-off rollback path
(feature flag `False` → forced legacy) also skips this check by
construction.

**What counts as "missing".** The runtime treats `None`, `""`, `[]`,
`{}`, and whitespace-only strings (`"   "`) as missing for
required-kwarg purposes. The whitespace-strip applies to strings only:
`isinstance(v, str) and not v.strip()` counts as missing alongside the
explicit empty cases. Authors who want "empty is legitimately valid"
should write a sentinel (e.g. the literal string `"none"` or a dict
like `{"opted_out": true}`) rather than an empty/whitespace collection.
This is the simpler rule; we keep it strict.

Runtime check, inserted in `response_node.py` between kwarg resolution
and the builder call:

```python
# Identify which catalog fields are required.
required_field_names = {
    f["name"] for f in (entry.get("fields") or [])
    if f.get("required") is True
}
def _empty(v):
    if isinstance(v, str): return not v.strip()
    return v is None or v == [] or v == {}
missing = sorted(
    name for name in required_field_names
    if _empty(resolved_kwargs.get(name))
)

if missing:
    mode = widget_cfg.get("on_missing_required", "error")
    if mode == "fallback_text":
        fallback = str(resolve_templates(widget_cfg.get("fallback_text") or "", state))
        if not fallback.strip():
            raise ValueError(
                f"response_node {widget_type!r}: on_missing_required=fallback_text "
                f"but fallback_text is empty/missing"
            )
        logger.warning(
            "[response_node_widget_fallback] node=%s widget=%s missing=%s",
            data.get("label") or "?", widget_type, missing,
        )
        variables = dict(state.get("variables") or {})
        variables["_response_text"] = fallback
        variables["_return_mode"] = "to_orchestrator"
        result["variables"] = variables
        return result
    raise ValueError(
        f"response_node {widget_type!r}: required kwargs missing {missing}. "
        f"Either populate them upstream (parse_node, tool_call_node, llm_node) "
        f"or set widget.on_missing_required=\"fallback_text\" with a fallback_text template."
    )
```

This keeps the deterministic story: no LLM is invoked on the fallback
path either; the agent designer wrote the fallback string. If they want
an LLM to paraphrase the failure, they put an `llm_node` upstream and
write its output to a slot that the fallback template references.

### 4.4 Template migration

Two files to migrate:

- `backend/app/agents/templates/transfer_money.chat.json` — 6 response_node
  blocks. Convert each from `data_template` to `kwargs`. Drop `payee_hint`
  (dead key). Keep all the other hint/type fields — they're now builder
  kwargs.
- `backend/app/agents/templates/refund_fee.chat.json` — N response_node
  blocks. Convert and drop `fee_type_hint`, `amount_hint` (both dead).

For each block:
```diff
- "title": "Confirm transfer",
- "data_template": {
+ "kwargs": {
+   "title": "Confirm transfer",
    "amount": "{{variables.amount}}",
    ...
-   "payee_hint": "{{variables.payee_hint}}"   // DEAD — drop
  }
```

**Breaking change: `title` moves under `kwargs`.** Today templates put
`title` at the widget top level (sibling of `data_template`). After
migration, `title` is a builder kwarg (every builder already accepts it)
and lives inside `kwargs`. The §4.5 "Convert to kwargs" affordance must
move the top-level `title` value into the kwargs dict and remove the
sibling. This is the same change every catalog-emitted widget will see,
and is the only authored-shape change that affects all templates rather
than just `transfer_form` / `refund_form`.

**Template scan: `actions` content.** Neither `transfer_money.chat.json`
nor `refund_fee.chat.json` declares an `actions: [...]` field in any
response_node widget config today (verified by grep). The conversion
path therefore doesn't need to drop or warn about template-supplied
actions — the builder's default actions take effect by simple omission.
Should a future template ship custom `actions` before this PR lands,
the conversion logic in §4.5 must drop them with an audit log and
explain that the builder now owns the actions array.

After migration, the DB rows backing these templates need a re-import via
`POST /api/agents/admin/import-file/<filename>` (or `import_template_file`
from a one-shot script). See §5 for the cache-refresh implications.

### 4.5 Author UI changes

`frontend/src/components/agents/graph/NodePropertiesPanel.jsx` —
`ResponseNodeEditor` for `return_mode === 'widget'`:

1. Fetch `/api/widgets/catalog` (already exposed via `catalog_for_api()`)
   and use the selected `widget_type`'s `fields` list to render a kwargs
   editor: one input per declared field, with type hints (`string`,
   `object`, `array`) shown inline. Fields with `required: true` are
   visually marked; an empty input warns inline.
2. Each input feeds a key in `widget.kwargs` (not `data_template`).
3. Add the upstream-state tree (§4.5.1) so the author can click directly
   from "what's available" to "what's required" — turning the kwargs
   editor into a wiring exercise.
4. Add a `on_missing_required` toggle (radio: error / fallback_text)
   plus a `fallback_text` textarea (with `{{}}` substitution + the same
   state-tree insert affordance). The textarea is hidden unless the mode
   is `fallback_text`.
5. Show a deprecation banner when an existing template still has
   `data_template` populated, with a one-click "Convert to kwargs" button.
   The conversion logic:
   - Move any top-level `title` field INTO `kwargs.title` (§4.4 breaking
     change). Delete the sibling.
   - Copy every `data_template` key that appears in the catalog `fields`
     for the chosen `widget_type` into `kwargs`.
   - For keys NOT in `fields`, leave them in `data_template` (so the
     template still works) and warn the author that they're dead/drift
     — list each with a "delete" affordance.
   - Once `data_template` is empty, drop it.

### 4.5.1 State visibility at the response_node (the design-time tree)

The hard question for the author is: *"what's in `state.variables` when
this response_node runs?"* The current `useUpstreamVariables` BFS
collects flat slot names (parse_node writes, tool_call output_var,
interrupt targets_slot), which is enough for `{{variables.X}}` insertion
but doesn't help the author find `{{variables.transfer_details.sourceAccounts}}`
— the nested path that the kwargs editor most often needs.

**Scope clarification.** The tree visualizes `SubAgentState.variables`
(the sub-agent's local scratchpad), NOT the main orchestrator's
`AgentState.variables`. Inside a sub-agent, the orchestrator's slots are
not visible. The author's responsibility ends at the sub-agent boundary.

Every nested shape we'd want to surface is already declared somewhere;
the BFS just needs to read it:

| Upstream node | Slot name source | Nested-shape source |
|---|---|---|
| `parse_node` (mode=regex) | `extractors[].slot` | extractor `type` → scalar (money/yes_no/etc.) |
| `parse_node` (mode=llm) | `writes` values | node's `output_schema` (already in `data`) |
| `tool_call_node` | `output_var` | the tool's `actions[<action>].output_schema` from `/api/tools` |
| `llm_node` (structured) | `output_var` | node's `output_schema` (already in `data`) |
| `interrupt_node` | `targets_slot` | scalar (string) |
| `tool_node` | (none — see below) | — |

**`tool_node` doesn't add slots.** `tool_node` executes tool_calls
emitted by the preceding `llm_node` and writes the results to
`state.messages` as ToolMessages — it does NOT write to
`state.variables`. The result is reachable via `messages.last_tool` in
the predicate DSL, but it's not a kwarg-able slot. Authors using the
`llm_node` → `tool_node` → `llm_node` ReAct loop pattern should know
that the loop closes when the second `llm_node` reads the tool's reply
from messages and emits its own output. If that output needs to feed a
downstream widget, the second `llm_node` must declare an `output_schema`
and an `output_var` — that's the slot the response_node consumes.

**State scalars** are always-available roots of the tree, alongside the
slot writers:

- `user_id` — string
- `session_id` — string
- `channel` — string (`"chat"` | `"voice"`)
- `iteration_count` — number (current ReAct loop iteration)
- `main_context.*` — any key the outer orchestrator passes in (varies
  per template — show a generic "main_context" node with `kind: unknown`)
- `messages.*` — derived view (`last_ai`, `last_user`, `last_tool`,
  `count`, etc.) per the predicate DSL

These scalars are resolvable via `resolve_templates` (top-level state
lookup in `app/utils/templates.py`) and should appear at the top of
the tree as fixed roots — distinct from the dynamic per-node slot
writers below them.

Implementation:

- **Backend.** `/api/tools` already returns `actions[].output_schema`
  for every AgentTool. No backend change. (Verify the schema is consistent
  shape — typically JSON Schema; document the convention if not.)
- **Frontend.** Extend `useUpstreamVariables` to fetch the tools catalog
  once (cache for the builder session) and return a tree, not a list.
  Each tree node carries `{name, kind, sourceNodeId, children?}` where
  `kind ∈ {slot, scalar, object, array, unknown}`.
- **Frontend.** Replace the flat list in `VariablesPanel` with a
  disclosure tree. Leaf click inserts the dotted path
  `{{variables.transfer_details.sourceAccounts}}` at the kwargs input's
  cursor. Object/array nodes can be inserted as a whole (root path) for
  cases like passing `source_options` as a list directly.
- **Frontend.** In the kwargs editor for the chosen widget, highlight
  *required* kwargs in red until the author wires them; green once
  wired. Mirror the runtime check at design time so the author sees the
  same gap analysis the loader runs.

Edge cases:
- A tool whose `output_schema` is missing or `{}` shows the slot as
  `kind: unknown` and the author can still type a nested path by hand.
- A slot written by *both* a parse_node and a tool_call_node (rare but
  possible — last-writer-wins per runtime semantics) shows both
  emitters in the tooltip; the tree shape comes from whichever has
  a richer declaration.

### 4.6 Template loader validation

`backend/app/agents/template_loader.py`:

Add the following checks in `_validate_structure` / `_validate_semantics`
for `response_node(return_mode == "widget")`:

1. **Catalog lookup.** `widget.widget_type` must exist in `WIDGET_CATALOG`
   and have a non-null `render_fn`. Hard fail at load time.
2. **Kwarg name check.** If `widget.kwargs` is present, every key in it
   must appear in the catalog's `fields` for that widget_type, OR be in
   the small allowlist of top-level widget properties that builders
   accept as kwargs but which aren't `data` fields:

   - `title` — sits at the top of every widget object (`widget.title`),
     not inside `widget.data`. The catalog's `fields[]` describes
     `widget.data` only, so `title` legitimately isn't there for most
     entries (only `text_card` and `generic_composite` happen to list
     it). Every catalog builder accepts `title` as a kwarg, so it must
     be allowlisted explicitly here.

   Hard fail on any other unknown key.
3. **Required-kwarg coverage.** For every catalog field with
   `required: true`, the template must EITHER (a) declare that field in
   `widget.kwargs` (the value may still resolve to None at runtime,
   handled by §4.3.1), OR (b) set `widget.on_missing_required ==
   "fallback_text"` with a non-empty `fallback_text`. Hard fail otherwise.
   This is the load-time mirror of the runtime check — catches "forgot
   to wire a required field" at template upload, not at first invocation.
4. **Fallback declaration.** If `widget.on_missing_required ==
   "fallback_text"`, then `widget.fallback_text` must be a non-empty
   string. Hard fail otherwise.
5. **Regulated guard.** If the template `is_regulated == true`, then
   `widget.on_missing_required` must be `"error"` (or unset, which
   defaults to error). Regulated flows fail loud — a silent text fallback
   in place of an audited widget is the wrong default. Hard fail.

   Note: `is_regulated` and `locked_for_business_user_edit` are two
   distinct columns on `SubAgentTemplate`. `is_regulated` is the
   compliance/audit flag and is the one we key on for this guard.
   `locked_for_business_user_edit` is the UI-level edit permission
   flag (regulated templates are typically also locked, but a template
   could be locked without being regulated, or vice versa). Don't
   conflate them.
6. **Legacy warning.** If `data_template` is present without `kwargs`,
   emit a load-time warning (don't fail) — eases migration.

These run alongside the existing predicate / post_write checks. They
make the loader the first defence; the runtime checks in §4.3 / §4.3.1
remain because catalog or builder changes can drift independently
between template uploads.

### 4.7 Tests

- `tests/widgets/test_response_node_kwargs.py` — table-driven tests for
  each migrated widget: build a synthetic state, run the response_node
  handler, assert the resulting `widget["data"]` shape against a snapshot.
- `tests/widgets/test_response_node_legacy_fallback.py` — feed a template
  with only `data_template` (no `kwargs`); assert (a) handler still
  produces output, (b) deprecation warning is logged once per template.
- `tests/widgets/test_response_node_fallback_text.py` —
  - state missing a catalog-required slot + `on_missing_required="error"`
    → raises `ValueError` with the missing field name.
  - same state + `on_missing_required="fallback_text"` + `fallback_text="..."`
    → returns `_return_mode == "to_orchestrator"` with the resolved text;
    no builder called; warning logged.
  - regulated template with `on_missing_required="fallback_text"` →
    loader rejects at template load (`TemplateValidationError`).
- `tests/widgets/test_template_loader_widget_validation.py` — covers
  loader checks 1–6 from §4.6: unknown widget_type, unknown kwarg name,
  missing required kwarg without fallback, fallback mode without
  `fallback_text`, regulated + fallback mode, legacy `data_template`
  warning.

## 5. Sequencing

1. **Builder + catalog updates** (4.1, 4.2) — additive, no downstream changes.
2. **response_node runtime change** (4.3) — dual-path; old templates still work.
3. **Template loader validation** (4.6) — warn-only for `data_template`.
4. **Template migrations** (4.4) — one PR per template, watched in staging.
5. **Author UI** (4.5) — once the runtime accepts `kwargs`.
6. **Remove legacy fallback** (revisit 4.3) — once both templates migrated
   and at least one release cycle has passed without legacy warnings in
   logs.

Steps 1–3 are landable in a single PR (small, mechanical). Steps 4–5 are
independent. Step 6 is a follow-up cleanup.

**Feature-flag rollback validity window.** `FEATURE_RESPONSE_NODE_BUILDER`
is a meaningful no-redeploy rollback **only between steps 2 and 4**.
The flag forces every response_node down the legacy `data_template`
path; once step 4 has migrated `transfer_money.chat.json` and
`refund_fee.chat.json` to use `widget.kwargs` (no `data_template`),
flipping the flag off causes the legacy branch to raise (no
`data_template` to fall back to). After step 4, rollback for the
migrated templates requires a code revert (PR revert + restart), not
a flag flip. On-call operators should treat the flag as a debug
toggle, not a perpetual safety net. Once we're past step 6, the flag
disappears entirely.

**Template reload on re-import.** `app/agents/template_store` does NOT
keep an in-process cache of LoadedTemplate objects — `list_templates()`
reads from the DB each call. However, the `_AGENT_TEMPLATE` registry in
`app/agents/__init__.py` (which maps `(agent_name, channel) →
template.name`) IS populated once at `init_agents()` and used by the
Planner / DynamicSubAgentTool. The write endpoints
(`POST/PUT/DELETE /api/agents`, `POST /api/agents/admin/import-file`)
already call `_refresh_registry()` which re-runs `init_agents()` and
refreshes Studio's `langgraph.json` — so re-imports via the API are
fully self-contained. **Direct DB pokes (a one-shot script writing
rows) require either a manual `init_agents()` call or a rolling
restart.** Prefer the import endpoint.

**Refresh atomicity.** `init_agents()` rebuilds `_AGENT_TEMPLATE` in
place via `setdefault` + dict-mutation (see
`app/agents/__init__.py:89-102`) — it does NOT swap a freshly-built
dict atomically. A request arriving mid-refresh can read partial
state for a few milliseconds: some `(agent, channel)` pairs may point
to the new template name while others still point to the old. The
window is short and self-healing (the next request reads the fully
updated registry), and we judge this acceptable for an admin /
template-author operation. If a stricter guarantee is needed (e.g.
high-RPS regulated flows during a re-import), the fix is to rebuild
into a local dict and assign-replace at the end of `init_agents()` —
not in scope here.

**Breaking change for template authors: `title` location.** Existing
authored templates (chat variants of any future widget-emitting
sub-agent) need the §4.4 `title`-into-kwargs move. The "Convert to
kwargs" affordance in §4.5 handles this for templates loaded into the
builder; templates edited as raw JSON outside the builder must do it
manually. Once Step 6 removes the legacy path, an unmigrated `title:
"..."` sibling becomes a load error rather than a deprecation warning.

## 6. Audit posture (resolved)

Regulated templates retain their audit guarantees under Position 2.
The composite property comes from four independent layers:

1. **No LLM in the widget path.** The builder is pure deterministic
   Python — given identical kwargs, it produces identical output. No
   token sampling, no nondeterminism, no possibility of paraphrase
   drift. The Presenter has had this property; sub-agents acquire it.
2. **Named-kwarg discipline.** Templates declare `widget.kwargs` as a
   flat dict of state paths → builder kwargs. Substitution happens
   only at those named slots. There is no free-form templating into
   nested widget structure — what the builder doesn't accept, the
   author cannot inject.
3. **Loader-time gap detection.** §4.6 check 3 rejects templates that
   miss a catalog-`required` field at upload time. A regulated
   template can't ship missing a required slot.
4. **Regulated-mode-must-error.** §4.6 check 5 bans
   `on_missing_required="fallback_text"` for regulated templates. If
   upstream data is missing at runtime, the graph halts loudly rather
   than silently swapping in a text response (which would not have
   been pre-reviewed by compliance).

Together these enforce: every regulated widget invocation is either a
fully-reviewed deterministic render or a halted turn. There is no third
state in which an un-audited surface reaches the user.

## 7. Open questions

1. **Should `metadata` move under builder ownership?** Today templates set
   `metadata: {flow: "chat", transfer_type: "m2m"}` and the action dispatch
   layer reads it. Position 2 leaves metadata as a free-form template
   field. If/when metadata acquires a schema, fold it into the builder.
2. **Multiple builder variants per widget_type?** `transfer_type` ∈
   `{m2m, cc, zelle}` is currently a kwarg, but could become three
   separate widget_types if the rendering diverges further. Decide once
   per widget at migration time, not generically.
3. **Tool output_schema convention.** §4.5.1 leans on
   `actions[].output_schema` being structured enough for the tree
   builder to disclose nested fields. Today the convention isn't
   strictly enforced — some tools return free-form dicts with no schema.
   Worth a short audit of `app/tools/*.py` to see how many tools have
   usable schemas, and whether a lint check is needed.
4. **Should `parse_node` (mode=llm) `output_schema` be required?**
   Currently optional. The state-tree story benefits a lot if it's
   declared. Either we (a) require it on regulated templates only, or
   (b) accept that some slots will be `kind: unknown` in the tree.

## 8. Acceptance

Migration is complete when:
- `transfer_form` and `refund_form` templates use `widget.kwargs` (with
  `title` moved under `kwargs`).
- `response_node` widget handler invokes the catalog builder when
  `settings.feature_response_node_builder` is `True` (default).
- `_validate_catalog()` round-trip succeeds with updated kwargs.
- Catalog entries for `transfer_form` and `refund_form` declare
  `runtime_fields` matching what the action handler injects post-submit.
- Frontend renders both widgets unchanged (no visual regression).
- Action handlers (`onTransferSubmit`, `onRefundSubmit`) still see
  `validation_id`, `_stage`, etc. populated correctly post-submit.
- Legacy `data_template` path still works (dual-path retained until
  step 6 of §5).
- **Feature flag works.** Setting
  `FEATURE_RESPONSE_NODE_BUILDER=false` forces every response_node back
  through the legacy path without a redeploy.
- **Fallback path works.** A response_node with
  `on_missing_required="fallback_text"` and an empty required slot emits
  the fallback text instead of a broken widget; no builder is called; a
  warning is logged.
- **Loader catches gaps at upload time.** Templates declaring widget
  kwargs that miss a catalog-required field (without `fallback_text`)
  are rejected with a clear message; regulated templates declaring
  `on_missing_required="fallback_text"` are also rejected.
- **State-tree author UX works.** In the Agent Builder, selecting a
  response_node shows a tree of upstream slots; tool-call outputs are
  expanded using the tool's `output_schema`; state scalars (`user_id`,
  `session_id`, `channel`) appear as tree roots; required widget kwargs
  are flagged red until wired.

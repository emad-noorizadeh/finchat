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
- Builder signature *is* the schema. TypeError on wrong kwargs.
- Templates declare an explicit `kwargs` map from state paths to builder
  kwargs — no shape responsibility on the author.

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

### Action-handler-populated keys

`TransferForm.jsx` reads `_stage`, `validation_result`, `confirmation_id`,
`effective_date`, `submit_error`. `RefundForm.jsx` reads `_stage`,
`decision`, `selected_activity_reference`, `submit_error`. None of these
appear in the initial render data — they're merged in by the widget action
handler post-submit. The builder owns initial state only; action handlers
own runtime mutations. Migration must not break this seam.

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

### 4.2 Catalog `fields` updates

For each widget that grew kwargs, mirror the new fields in
`WIDGET_CATALOG[*]["fields"]` so the Agent Builder UI can surface them.
`sample_data` should also gain the new keys with realistic values so the
`/widgets` preview reflects production shape.

`_validate_catalog()` round-trip test catches signature mismatches once
`sample_build_args` is updated to pass any required new kwargs.

### 4.3 `response_node.py` runtime change

Replace lines 69–76 in `backend/app/agents/nodes/response_node.py`:

```python
if return_mode == "widget":
    from app.widgets.catalog import WIDGET_CATALOG

    widget_type = widget_cfg.get("widget_type") or ""
    entry = WIDGET_CATALOG.get(widget_type)

    if entry is None or not callable(entry.get("render_fn")):
        raise ValueError(
            f"response_node {widget_type!r}: unknown widget_type or no render_fn in catalog"
        )

    # Position-2: call the builder. Templates declare a `kwargs` map
    # from state paths to builder kwargs; we resolve each through the
    # shared template engine then unpack.
    raw_kwargs = widget_cfg.get("kwargs") or {}
    if not isinstance(raw_kwargs, dict):
        raise ValueError(
            f"response_node {widget_type!r}: widget.kwargs must be a dict, got {type(raw_kwargs).__name__}"
        )

    resolved_kwargs = {k: resolve_templates(v, state) for k, v in raw_kwargs.items()}

    # Legacy fallback (dual-path during migration). Authors who still
    # use `data_template` get the old behavior with a deprecation log.
    if not raw_kwargs and widget_cfg.get("data_template"):
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
        try:
            widget = json.loads(entry["render_fn"](**resolved_kwargs))
        except TypeError as e:
            raise ValueError(
                f"response_node {widget_type!r}: builder rejected kwargs ({sorted(resolved_kwargs)}): {e}"
            )
        # Templates may still want to override metadata; merge if provided.
        if widget_cfg.get("metadata"):
            widget = {**widget, "metadata": {**(widget.get("metadata") or {}), **widget_cfg["metadata"]}}

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
- "data_template": {
+ "kwargs": {
    "amount": "{{variables.amount}}",
    ...
-   "payee_hint": "{{variables.payee_hint}}"   // DEAD — drop
  }
```

After migration, the DB rows backing these templates need a re-import via
`POST /api/agents/admin/import-file/<filename>` (or `import_template_file`
from a one-shot script).

### 4.5 Author UI changes

`frontend/src/components/agents/graph/NodePropertiesPanel.jsx` —
`ResponseNodeEditor` for `return_mode === 'widget'`:

1. Fetch `/api/widgets/catalog` (already exposed via `catalog_for_api()`)
   and use the selected `widget_type`'s `fields` list to render a kwargs
   editor: one input per declared field, with type hints (`string`,
   `object`, `array`) shown inline.
2. Each input feeds a key in `widget.kwargs` (not `data_template`).
3. Add the existing `VariablesPanel` "Insert variable" affordance — click
   a slot row to drop `{{variables.X}}` into the focused kwarg.
4. Show a deprecation banner when an existing template still has
   `data_template` populated, with a one-click "Convert to kwargs" button
   (best-effort: copy keys present in catalog `fields`, warn on extras).

### 4.6 Template loader validation

`backend/app/agents/template_loader.py`:

Add a structural check in `_validate_structure` for response_node widget
mode: if `widget.kwargs` is present, every key must appear in the
catalog's `fields` list for that `widget_type` (or be in a small allowlist
of always-permitted keys like `title`). If `data_template` is present
without `kwargs`, emit a warning (don't fail) — eases migration.

We also re-run the same kwarg-name check the builder would do, but at
load time, to fail fast on template upload instead of at first invocation.

### 4.7 Tests

- `tests/widgets/test_response_node_kwargs.py` — table-driven tests for
  each migrated widget: build a synthetic state, run the response_node
  handler, assert the resulting `widget["data"]` shape against a snapshot.
- `tests/widgets/test_response_node_legacy_fallback.py` — feed a template
  with only `data_template` (no `kwargs`); assert (a) handler still
  produces output, (b) deprecation warning is logged once per template.
- `tests/widgets/test_template_loader_kwarg_validation.py` — feed a
  template with a kwarg that isn't in `WIDGET_CATALOG[x]["fields"]`;
  assert `TemplateValidationError`.

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

## 6. Open questions

1. **Should `metadata` move under builder ownership?** Today templates set
   `metadata: {flow: "chat", transfer_type: "m2m"}` and the action dispatch
   layer reads it. Position 2 leaves metadata as a free-form template
   field. If/when metadata acquires a schema, fold it into the builder.
2. **Regulated templates' widget mode.** Today loader forbids
   `to_presenter` for regulated and allows widget. Is the schema
   enforcement we get from this migration sufficient for audit? Likely
   yes — the builder produces deterministic output from named kwargs, no
   LLM, no free-form substitution outside the named slots.
3. **Multiple builder variants per widget_type?** `transfer_type` ∈
   `{m2m, cc, zelle}` is currently a kwarg, but could become three
   separate widget_types if the rendering diverges further. Decide once
   per widget at migration time, not generically.

## 7. Acceptance

Migration is complete when:
- `transfer_form` and `refund_form` templates use `widget.kwargs`.
- `response_node` widget handler invokes the catalog builder.
- `_validate_catalog()` round-trip succeeds with updated kwargs.
- Frontend renders both widgets unchanged (no visual regression).
- Action handlers (`onTransferSubmit`, `onRefundSubmit`) still see
  `validation_id`, `_stage`, etc. populated correctly post-submit.
- Legacy `data_template` path still works (dual-path retained until step 6).

# `get_profile_data` — tool reference

A Planner-level, always-loaded, no-argument data tool that returns a compact view of the logged-in user's profile. The LLM calls it to answer questions like *"Who am I?"*, *"What's my rewards tier?"*, *"Show my credit score"*. The Presenter / fast-path uses the same call to feed `render_profile_card` without a second tool invocation.

This doc covers everything from disk to LLM, plus the gaps worth knowing before extending the tool.

---

## 1. At a glance

```
Disk: backend/profile/<user>_profile.json
   │
   │  read once at login (POST /api/login/<id>)
   ▼
profile_service._profile_data[login_id]    (in-memory dict, process-lifetime)
   │
   │  get_profile(user_id)  → 29-key profile dict
   ▼
GetProfileDataTool.execute(input={}, context={user_id, ...})
   │
   │  cherry-picks 8 fields → json.dumps(...)
   ▼
ToolResult(to_llm="{...8 fields...}")
   │
   ├─► ToolMessage(content=to_llm)  → state.messages → LLM sees on next turn
   └─► state.variables.profile_data = json.loads(to_llm)  → Presenter / render tools
```

**One call, two consumers.** The LLM reads the JSON string as prose; the renderer reads the parsed dict from a state slot. No second tool call needed to hand data to a widget.

---

## 2. Data source + lifecycle

### 2.1 Where the profile lives

| Stage | Location | When populated | Scope |
|---|---|---|---|
| **At rest** | `backend/profile/{alex,aria_bank,chris}_profile.json` | Authored offline | File-backed |
| **In memory** | `app.services.profile_service._profile_data[login_id]` | At login | Process-lifetime; cleared on restart |
| **Per call** | `context["user_id"]` → `get_profile(user_id)` | Every tool invocation | Read-only |

The file layout is `{"profile": {…29 keys…}, "accounts": [...]}`. Login reads it once, stashes the `profile` dict + `accounts` list into the module-level `_profile_data` dict, and keeps the file handle closed. All subsequent reads (this tool, `get_accounts_data`, the main orchestrator's system prompt) go against that in-memory dict — **no re-read from disk**.

### 2.2 `context["user_id"]` — where it comes from

Set by the main orchestrator when the graph runs:

```python
input_state = {
    "messages": [HumanMessage(content=req.content)],
    "user_id": req.user_id,      # ← login id from the POST /chat payload
    "session_id": session_id,
    ...
}
```

`tool_execute` passes this verbatim to every tool via the `context` dict. The tool itself has **no `user_id` arg** — the LLM can't pass a different one. That's the intended containment boundary.

---

## 3. Tool contract

Defined in `app/tools/data_tools.py:GetProfileDataTool(BaseTool)`.

| Attribute | Value | Meaning |
|---|---|---|
| `name` | `"get_profile_data"` | Stable handle the LLM calls |
| `always_load` | `True` | Bound to the Planner every turn; no `tool_search` needed |
| `should_defer` | `False` | Not deferred (see Planner's tool-loading rules) |
| `channels` | `("chat", "voice")` | Available in both channels |
| `output_var` | `"profile_data"` | `tool_execute` writes the parsed JSON here |
| `is_read_only` | `True` | No mutation |
| `is_concurrency_safe` | `True` (default) | Can run in parallel with other safe tools |
| `input_schema` | `{"properties": {}}` | Takes no arguments |
| `activity_description` | `"Looking up profile..."` | Shown in the "thinking" bubble during execution |
| `search_hint` | `"user profile name rewards tier credit score"` | Used by `tool_search` when deferred |
| `flow` | 3-step summary tuple | Surfaced on `/tools` page |

Registration happens via `register_tool(GetProfileDataTool())` at the bottom of the module, imported by `app.tools.init_tools()` at startup.

---

## 4. `execute()` — step by step

```python
async def execute(self, input: dict, context: dict) -> ToolResult:
    from app.services.profile_service import get_profile

    user_id = context.get("user_id", "")
    profile = get_profile(user_id)
    if not profile:
        return ToolResult(to_llm=json.dumps({"error": "Profile not loaded."}))

    name_info = profile.get("profileName", {})
    address   = profile.get("mailingAddress", {})
    rewards   = profile.get("rewardsProfile", {})
    scores    = profile.get("scoreDetails", [])

    profile_data = {
        "name":              name_info.get("userName") or name_info.get("firstName", ""),
        "city":              address.get("city", ""),
        "state":             address.get("state", {}).get("value", ""),
        "segment":           profile.get("businessSegment", {}).get("name", ""),
        "rewards_tier":      rewards.get("tierDisplayName") or "Standard",
        "qualifying_balance": rewards.get("qualifyingBalance", 0),
        "credit_scores":     scores[:3] if scores else [],
        "language":          profile.get("userLanguagePref", {}).get("value", ""),
    }
    return ToolResult(to_llm=json.dumps(profile_data))
```

Nothing is cached beyond the in-memory dict from login. Every call re-runs the field-picking.

### 4.1 Field source table

| Output key | Source path (in raw profile JSON) | Fallback |
|---|---|---|
| `name` | `profileName.userName` → `profileName.firstName` | `""` |
| `city` | `mailingAddress.city` | `""` |
| `state` | `mailingAddress.state.value` | `""` |
| `segment` | `businessSegment.name` | `""` |
| `rewards_tier` | `rewardsProfile.tierDisplayName` | `"Standard"` |
| `qualifying_balance` | `rewardsProfile.qualifyingBalance` | `0` |
| `credit_scores` | `scoreDetails[:3]` | `[]` |
| `language` | `userLanguagePref.value` | `""` |

`credit_scores` is passed through verbatim — each element is a dict (score provider, score, date, …) — the LLM infers the shape.

### 4.2 Typical output (for the `aryash` seed)

```json
{
  "name": "Aryash",
  "city": "Texas",
  "state": "TX",
  "segment": "Consumer",
  "rewards_tier": "Premium",
  "qualifying_balance": 45230.75,
  "credit_scores": [
    {"provider": "FICO", "score": 782, "date": "2026-03-01"}
  ],
  "language": "English"
}
```

~300–600 characters. Well under the `[tool_result_size]` threshold (~20k chars ≈ 5k tokens).

---

## 5. How the result reaches the LLM and the renderer

`tool_execute.run_one()` in `app/agent/nodes.py`:

```python
result = await tool.execute(tc["args"], context)
llm_text = result.to_llm                        # the JSON string
ToolMessage(content=llm_text, tool_call_id=tc["id"])
```

The `ToolMessage` is appended to `state.messages`. The LLM sees it verbatim on its next turn.

Then:

```python
if tool_obj.output_var:                         # "profile_data"
    parsed = json.loads(llm_text)
    state.variables[tool_obj.output_var] = parsed
```

Two observers:
- **LLM channel** — reads the JSON string to reason / narrate / answer.
- **Renderer channel** — `state.variables.profile_data` holds the parsed dict. `render_profile_card(profile_slot="profile_data")` picks it up without re-calling the tool.

The Presenter (fast-path) exploits this: when the Planner emits `get_profile_data() + present_widget()` in the same turn, the renderer reads `profile_data` straight from the slot and emits a `profile_card` widget — no second tool round-trip.

---

## 6. Related tools (for completeness)

Same file, same pattern:

| Tool | Output var | Source | Compact output |
|---|---|---|---|
| `get_profile_data` | `profile_data` | `get_profile(user_id)` | 8-field dict |
| `get_accounts_data` | `accounts_data` | `get_accounts(user_id)` | list of account dicts trimmed to `displayName`, `type`, `balance`, `available`, `accountTempId` |
| `get_transactions_data` | `transactions_data` | `get_transaction_records(user_id)` | list of transaction dicts (date, amount, merchant, category, …) |

All three share the contract: `always_load` or `should_defer=True`, no widget emission, write to a `state.variables` slot, let the Presenter render.

---

## 7. Where to find things

| File | Role |
|---|---|
| `app/tools/data_tools.py` | `GetProfileDataTool` class + registration |
| `app/services/profile_service.py` | `_profile_data` in-memory dict; `load_profile`, `get_profile`, `get_accounts`, `is_loaded` |
| `app/routers/auth.py` | `POST /api/login/{id}` — triggers `load_profile` |
| `app/agent/nodes.py:tool_execute.run_one` | Where `to_llm` becomes a `ToolMessage` and `output_var` lands in `state.variables` |
| `app/agent/state.py` | `AgentState.variables: dict` — the slot storage |
| `backend/profile/*_profile.json` | The seed JSON files (3 users) |

---

## 8. Gaps and limitations

Real things to know before extending this tool or shipping a new seed user.

### 8.1 In-memory only; no TTL, no reload

- `_profile_data` is a process-local dict. **A backend restart wipes it**, and the user has to log in again to repopulate. No DB, no cache layer.
- There is no freshness check. If the underlying profile file on disk changes, the in-memory copy keeps the old data for the session.
- **Mitigation path**: introduce a `SubAgentProfile` SQLModel or a small cache with a TTL that re-reads the file; call from `load_profile`.

### 8.2 The field pick-list is hardcoded

- To surface a new field to the LLM (e.g. `taxIdInfo`, `goals`, `serviceFeatureFlags`), you edit the Python dict literal in `execute()` and redeploy. No per-user or per-channel configuration.
- No audit log for what the LLM is allowed to see.
- **Mitigation path**: move the pick-list into a config (YAML/JSON/DB) keyed by `(user_segment, channel)`, scoped by compliance review.

### 8.3 No output schema → the LLM has to infer

- `output_schema` is not declared on the tool. The LLM sees `{"name":"…", "credit_scores":[{…}]}` as a raw JSON string and must infer field meanings from the keys.
- Anthropic / OpenAI tool-calling both accept an output schema; declaring one lets the LLM bind fields more reliably and lets downstream render tools validate the payload.
- **Mitigation path**: add `output_schema` to `GetProfileDataTool` (+ to `ToolResult.to_llm` validation), mirror the shape in `render_profile_card`'s catalog entry.

### 8.4 Empty-vs-missing isn't distinguishable

- When `mailingAddress` is missing, `city` lands as `""`. The LLM reads "city is empty" but can't tell whether the address is genuinely empty or we have no data.
- Same for `state`, `segment`, `language`.
- **Mitigation path**: use `None` (JSON `null`) for "not present" vs `""` for "present but blank". Today the tool conflates the two.

### 8.5 `scoreDetails[:3]` is positional, not chronological

- Returns the first three entries in array order, not the most recent. If the seed file isn't sorted by date, the LLM may surface stale scores.
- **Mitigation path**: sort `scoreDetails` by `date` descending before slicing, or return all and let the Presenter trim.

### 8.6 Error shape overloads the slot

- On "profile not loaded", `to_llm = '{"error": "…"}'`. That parses as JSON, so `state.variables.profile_data = {"error": "…"}`.
- A render tool that reads the slot and does `profile.get("name")` sees `None` and silently renders an empty card; there's no explicit ERROR flag to distinguish success from the no-op failure.
- The ERROR-dict convention used elsewhere in the codebase (`{"status": "ERROR", "error_category", "user_facing_message"}`) isn't applied here.
- **Mitigation path**: adopt the same ERROR shape; have `render_profile_card` check `if slot.get("status") == "ERROR"` before rendering.

### 8.7 No per-channel trimming

- Voice and chat receive the same JSON. A voice-first answer doesn't need `credit_scores[]`, a chat might want more.
- **Mitigation path**: branch on `context["channel"]` in `execute`, return a slimmer slice for voice.

### 8.8 No pagination / selective-field projection

- Always returns all 8 fields. LLM can't ask for *"just the tier"* — it gets everything.
- **Mitigation path**: introduce an optional `fields: list[str]` input arg; default to the full 8 when not supplied.

### 8.9 No direct test coverage

- Exercised only through the orchestrator integration tests. No unit test asserting the 8-field shape, the null fallbacks, or the `profile_data` slot write.
- **Mitigation path**: add `tests/tools/test_get_profile_data.py` with fixtures for each seed profile and the no-profile case.

### 8.10 Size observability is passive

- `tool_execute` logs `[tool_result_size]` only when the result exceeds ~20k chars. No metric, no dashboard, no alert.
- If someone inadvertently added a large `goals` array to the pick-list the warning would appear in logs but not surface elsewhere.
- **Mitigation path**: bump the warning to a counter metric (`tool.result.bytes{tool=get_profile_data}`), alert on percentile growth.

### 8.11 No reaction to logged-out state

- If `user_id` is empty (session not authenticated) the tool returns `{"error": "Profile not loaded."}`. The LLM sees a JSON string it can reason over. There's no explicit "redirect the user to login" behavior. In chat today this never happens because the router rejects unauthenticated requests upstream.
- **Mitigation path**: rely on the router check and document it; alternatively raise a specific `AuthError` that `tool_execute` translates into a standardized error ToolMessage.

---

## 9. Extending the tool

### 9.1 Add a field visible to the LLM

1. In `execute()`, pluck the new path from `profile` and add it to the `profile_data` dict.
2. If the new field affects widget rendering, add it to `render_profile_card`'s catalog entry in `app/widgets/catalog.py`.
3. (Optional) add to `app/widgets/summarizers.py:widget_to_llm_profile_card` if it matters for voice paraphrases.
4. Update this doc's §4.1 table.

### 9.2 Expose a richer view as a new tool

Prefer a new tool over extending this one. For example `get_profile_contact_details` that returns `phones`, `email`, `mailingAddress` in full. Keep `get_profile_data` focused on the card-level fields; anything heavier goes under its own tool so the LLM can decide when the cost is worth it.

### 9.3 Move the field list into config

Create `app/tools/config/profile_fields.json`:
```json
{
  "default": ["name", "city", "state", "segment", "rewards_tier", "qualifying_balance", "credit_scores", "language"],
  "voice":   ["name", "rewards_tier"]
}
```

Read from `execute()` based on `context["channel"]` and `context.get("segment")`. Gates the change behind a PR review without code rewrites.

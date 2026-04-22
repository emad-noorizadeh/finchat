# Sub-agent Phase 1 followups

Items intentionally deferred from plan v5 to focused PRs. Numbering keeps
parity with the review that proposed them (v4 review §*, v5 §R*).

## Resolve during PR review (small, local)

- **Resolver `cache_invalidation` knob** (v4 review §3). Add
  `Literal["per_procedure", "per_call"]` on `register_resolver`. Phase 1
  ships per-procedure only; per-call lands with the first time-sensitive
  resolver.
- **Category-differentiated fallback messages** (v4 review §7). Today both
  POLICY_BLOCK and UNEXPECTED_ERROR default to a single generic. Split:
  - POLICY_BLOCK: "I wasn't able to complete that — the account or amount isn't eligible right now."
  - UNEXPECTED_ERROR: "Something went wrong on our end — please try again in a moment."
  Already wired into `procedure_runtime._map_tool_result_to_sub_agent_result`
  as of commit 12b; verify copy in the Transfer template PR.
- **`user_facing_summary` on success** (v4 review §14). `SubAgentResult.widget`
  and `.glass` are the visible surface today; a parallel text field for the
  orchestrator LLM's acknowledgment would be useful. Land with whichever PR
  first demonstrates a need (probably Phase 2 metrics work).
- **Channel-aware `{{var}}` resolution** (v4 review §12). `Option` values
  render as `display` on chat and `label` on voice when used in `{{var}}`
  substitution. Already implemented inside confirm_step's `_render_ready`;
  generalize into `resolve_templates` helper when the second regulated
  template reuses the pattern.

## Phase 2 scope

- **Narrow LLM escape classifier** (plan v5 §R3). Today's keyword-only
  catches abort/topic-change only when users are explicit. Phase 2 adds a
  single constrained-output LLM call for fuzzier phrasings ("hmm let me
  think", "wait a sec, no").
- **Per-slot + per-procedure timeouts** (plan v5 §R8). Add `SlotSpec.timeout_ms`
  and template-level `procedure_timeout_ms`. Voice especially needs this
  (silent caller on an open line).
- **Metrics emission** (plan v5 §R15). Counters for slot fills per tier,
  extraction failure rate, escape rate, correction rate, POLICY_BLOCK rate
  by category, procedure duration histogram. Same PR as the LLM escape
  classifier — single observability pass.
- **Batched `slot_form` widget for chat** (v3 §R11). `slot_collector` emits
  a single form card instead of one-slot-per-message when the template
  declares `rendering: "batched"`.
- **Predicate engine for conditional slots** (plan v2 §R3/§R11). JSONLogic or
  similar over `variables`. Enables `slot.required` predicates and cleaner
  `step_up_auth`-equivalent trigger patterns (if that category ever returns
  under a different name — currently forbidden by principle #2).
- **Corrections confirm-before-wipe for ambiguous cases** (v4 review §6).
  When the detector fires via keyword+value inference (not explicit slot
  name), emit a one-word confirmation before cascading. "Change from_account
  to the other checking?" → yes/no. Adds 1 turn in ambiguous cases; prevents
  silent wipe.

## Phase 3 scope

- **Authoring UI split** — Template Editor (engineer) vs Template Config
  (business user). `locked_for_business_user_edit` is enforced in the backend
  today (commit 8 template_loader); UI surfaces it in Phase 3.
- **Template version pinning for in-flight procedures** (plan v2 §1
  open-decision). Session-level flag snapshot ships in Phase 1 (commit 15
  wiring); generalize in Phase 3 so all templates pin their version at
  procedure entry.
- **i18n for template copy fields** (v4 review §17). `prompt`,
  `correction_prompt`, `summary_template`, `unsupported_channel_message`,
  `user_facing_message` should route through a locale layer.

## Policy / product decisions (not code)

- **Voice disambiguation for N>3 options** (v4 review §8). Ask product for
  the 95th-percentile eligible-account count per user. If ≥8, add paginated
  options or "say the last four digits" free-text fallback.
- **`suppress_collected_state_on_fail: false` for non-regulated templates**
  (v4 review §13). Opt-out requires a code-review checklist entry explaining
  why exposing slot values is safe for the specific template. No Phase 1
  template uses this; when the first non-regulated template lands, add the
  checklist to the PR template.
- **Nested sub-agents** (plan v5 locked principle #14). Forbidden in Phase 1.
  If composition becomes necessary, design it as template-level composition
  (one template embedding primitives from another), not graph-nested
  sub-agent invocation.

## Minor cleanup

- **Enumerate procedure state fields in `ProcedureState` docstring**
  (plan v5 §R14 commit 12a). Done inline in `procedure_runtime.py` header.
- **Full-length template hash** (plan v4 §R12). Returned by `template_hash()`
  as 64-char SHA-256. Logs truncate for display.
- **Log event naming consistency** (plan v4 §R13). `[subagent_*]` prefix
  throughout.
- **Escape observability** (plan v4 §R15). Every classification logs at
  DEBUG with a sha1-prefix utterance hash. Flip to DEBUG when investigating.

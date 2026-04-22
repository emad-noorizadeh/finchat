# Sub-agent template rollout runbook

Covers the flag-gated rollout of regulated sub-agent templates (starting with
`domestic_transfer_v1`). Written against plan v5 locked principles.

## Flag semantics

- **`TRANSFER_USE_TEMPLATE=true`** (boolean env/flag) → new sessions start on
  the template-driven path; existing legacy `TransferChatAgent`/
  `TransferVoiceAgent` remain available.
- Flag is **snapshotted at session creation** (`POST /api/chat/sessions`) into
  the `ChatSession.template_overrides` column. Subsequent turns read from the
  snapshot; the flag is not re-evaluated per turn.
- In-flight sessions **always complete on the version they started with**.

## Rolling out

1. Deploy backend containing the template path.
2. Verify `[subagent_enter.v1] template=domestic_transfer_v1` shows up in logs
   on a test session with the flag on.
3. Gradually increase the fraction of new sessions with the flag true (canary
   % → 100% over a release window).
4. Watch: `subagent.completions{status=completed, reason=success}` stays
   flat or rises; `subagent.completions{reason=policy_block}` matches baseline
   tool-failure rate; no spike in `subagent.completions{reason=retry_exhausted}`.

## Rollback (P0 bug discovered post-launch)

If the new template has a P0 bug:

1. **Flip the flag to false.** New sessions fall to legacy from creation.
   Verify: create a new session, `grep '[subagent_enter.v1]'` returns
   nothing for that session; legacy's `[transfer_agent_*]` (or equivalent)
   logs appear instead.

2. **Accept that in-flight sessions complete on buggy path.** Flag is
   snapshotted per session. They either complete or time out naturally.

3. **If (2) is intolerable** (data corruption risk, compliance incident):
   Database-level clear of `template_overrides` for affected sessions:

   ```sql
   UPDATE chatsession
      SET template_overrides = NULL
    WHERE template_overrides->>'domestic_transfer'->>'version' = 'v1_template'
      AND created_at > <cutover_timestamp>;
   ```

   This is a **manual, reviewed** step. Never automate. Next turn on an
   affected session re-evaluates the flag and falls to legacy.

4. **Old classes stay deployed for one release window** after the new path
   reaches 100% rollout. They're removed in the subsequent release once all
   in-flight legacy sessions have drained.

## Verification queries

**Confirm new sessions use template:**
```
grep '\[subagent_enter\.v1\] template=domestic_transfer_v1' <log-file>
```

**Confirm rollback worked (no new template entries after flag flip):**
```
grep '\[subagent_enter\.v1\] template=domestic_transfer_v1' \
  --after-context=0 <log-file> | awk '$2 > "<flag_flip_timestamp>"' | wc -l
# Expect: 0 for sessions created after the flip
```

**Watch POLICY_BLOCK rate** (regressions on tool-eligibility checks):
```
grep '\[subagent_exit\.v1\].*policy_block' <log-file> | wc -l
```

## What NOT to do

- **Do not** amend a running template's JSON hot (bypassing the flag). The
  template hash is snapshotted per procedure run in Phase 3 but not Phase 1;
  in-flight changes produce undefined behavior.
- **Do not** delete legacy classes before the rollback window elapses.
- **Do not** add new slot types or node types as a hotfix. Those are code
  changes that require review + test + deploy.

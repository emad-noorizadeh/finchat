# Backend Deploy Runbook

How to deploy a new backend image and manage the SQLite database in
production (OpenShift) and locally during development. Read this entirely
before your first deploy.

The backend uses SQLite. The DB is a single file — locally at
`backend/data/app.db`, in prod at `/app/data/app.db` on a PersistentVolume.
Schema is managed by Alembic; see `backend/migrations/` for the migration
chain and `backend/app/database.py:run_migrations` for the boot-time hook.

---

## TL;DR — every prod deploy

1. Announce maintenance / freeze traffic.
2. **Back up the database** (see "Backup procedure").
3. Trigger the rollout (scale to 0, update image, scale to 1).
4. Watch pod logs until lifespan reports startup complete.
5. If the release includes migrations, verify the schema (see
   "Migration verification").
6. If the release includes a content change to any seeded sub-agent template
   (e.g. `transfer_money_chat.json` updated in the PR), explicitly apply each
   changed file via the admin import endpoint (see "Agent template
   deploys"). File changes are NOT auto-deployed by the new image.
7. Unfreeze.

If anything looks wrong: scale to 0, restore the backup, redeploy the
previous image, scale to 1.

---

## Backup procedure

Always back up before any migration deploy. Backups are free; recovery
from a bad migration without one is not.

### Locally (dev machine)

```bash
cd backend
./scripts/backup_db.sh data/app.db
# → Backup created: data/app.db.backup.20260512-143022
```

This works whether the backend is running or stopped — `backup_db.sh`
uses SQLite's online backup API (`.backup`) for a transactionally
consistent snapshot.

### In prod (OpenShift)

```bash
# Find the running pod
oc get pods -l app=backend
# → backend-7d5c8f4b8-xyzab   1/1   Running

# Trigger an online backup inside the pod
oc exec backend-7d5c8f4b8-xyzab -- \
  /app/scripts/backup_db.sh /app/data/app.db

# Pull a copy down to your laptop for off-cluster safety
oc cp backend-7d5c8f4b8-xyzab:/app/data/app.db.backup.20260512-143022 \
  ./prod-backup-20260512-143022.db
```

The backup file lives on the same PV as the live DB until you delete it.
Pulling a copy off-cluster is the safety net against PV corruption.

### Restoring a backup

> **Important:** restoring is a byte-for-byte file copy. The restored DB
> has whatever schema, data, and `alembic_version` it had at backup time.
> See "Background — what restore actually does" at the bottom of this doc.

#### Locally

```bash
# 1. Stop the backend (Ctrl-C in its terminal).

# 2. Remove SQLite's WAL sidecars if present — these belong to the LIVE
#    DB and would put the restored file into an inconsistent state.
#    The journal file is created in non-WAL mode; either may exist.
rm -f data/app.db-wal data/app.db-shm data/app.db-journal

# 3. Copy the backup over the live DB.
cp data/app.db.backup.20260512-143022 data/app.db

# 4. Start the backend.
python run.py
```

#### In prod

```bash
# Stop the backend
oc scale deployment/backend --replicas=0

# Copy the backup over the live DB
oc cp ./prod-backup-20260512-143022.db <some-pod-with-pv>:/app/data/app.db.new
oc exec <some-pod-with-pv> -- mv /app/data/app.db.new /app/data/app.db
```

Then bring backend up — see "Recovery — full rollback" or
"Recovery — restoring data without rolling back schema" for whether to
use the current image or revert to a previous tag.

### Backup retention

No automated retention yet. Conventions until that lands:

- Keep at least the last 3 prod backups on the PV. Delete older ones
  during the deploy window.
- Pull a copy of any pre-migration backup down to your laptop or a
  shared drive — that's your insurance against PV corruption.

---

## Test a migration locally

Use this flow when you've pulled a branch that adds a new file under
`backend/migrations/versions/` and you want to validate it against your
own local data **before** running the backend on it (and before any
prod deploy). This is what dev `<--->` migration sanity-checking looks
like end-to-end.

```bash
cd backend
source .venv/bin/activate

# 1. Back up your local DB — every migration test starts here.
./scripts/backup_db.sh data/app.db
# → Backup created: data/app.db.backup.<timestamp>
#   Note the filename — you may need it for rollback in step 6.

# 2. (Read-only) inspect what's pending.
alembic current      # where the DB is now (the previous head)
alembic heads        # what the new migration chain ends at
alembic history      # full chain (most-recent first)

# 3. Apply the pending migrations.
./scripts/migrate.sh
# Each "Running upgrade <prev> -> <next>" line is one migration applied
# inside a transaction. If any one fails, alembic rolls back THAT
# migration only; earlier ones in the same run stay committed.

# 4. Verify the schema is at head and key tables look right.
./scripts/verify_db.sh data/app.db
# Confirm the "alembic current" output shows the new revision tagged "(head)".

# 5. Run the backend and exercise the changed code paths.
python run.py
# Send a chat message in the UI, hit any endpoint that touches the new
# schema, run any relevant tests:
# pytest tests/
```

If everything works, you're done — the migration is safe to ship.

### Rollback if something breaks

Two paths, pick based on whether you care about data written during your
test.

**(a) Reverse the migration with alembic** — preferred for routine
rollback. Keeps any data you added during testing, just undoes the schema
change.

```bash
# Stop the backend (Ctrl-C).
alembic downgrade -1                 # walk back one revision
# Or to a specific revision:
alembic downgrade <previous-hash>    # use a prefix from `alembic history`

./scripts/verify_db.sh data/app.db   # confirm you're back on the old head
python run.py                        # restart on old code path
```

This requires the migration's `downgrade()` function to be correct. If
`downgrade()` is missing or broken, fall through to (b).

**(b) Restore the backup file** — heavier; discards everything that
happened in the DB since step 1's backup. Use this if `downgrade()` is
broken or you just want a clean reset.

```bash
# Stop the backend (Ctrl-C).
# Strip SQLite WAL/journal sidecars belonging to the live DB:
rm -f data/app.db-wal data/app.db-shm data/app.db-journal
cp data/app.db.backup.<timestamp> data/app.db
python run.py
```

### What `./scripts/migrate.sh` actually does

It's a one-liner around `alembic upgrade head` that activates the venv
first. The backend's lifespan calls the same `alembic upgrade head` via
`run_migrations()` at boot — running the script manually just applies the
migrations **before** you start the backend so you can verify the schema
without serving any requests.

If you skip the script and just run `python run.py`, lifespan applies
the migrations during startup. That's functionally equivalent except
that you can't `verify_db.sh` between "apply" and "serve" — the backend
is already serving by the time you'd run it.

### What if the new migration was authored by me (not pulled)?

Then you're in author flow, not test flow. See "Authoring migrations"
near the bottom of this doc — same scripts, plus you'll generate the
revision file first with `alembic revision --autogenerate`.

---

## Standard deploy (no migrations)

Use this when the PR doesn't touch `backend/migrations/versions/`.

### Locally

There's no "deploy" locally — you're already running against your code.
Just restart the backend after pulling new code:

```bash
# In the terminal running the backend
Ctrl+C
git pull
cd backend && source .venv/bin/activate
./scripts/migrate.sh   # no-op if no new migrations; safe to run always
python run.py
```

### In prod

```bash
# 1. Announce maintenance, optionally block at the route level.

# 2. Backup just in case
oc exec <pod> -- /app/scripts/backup_db.sh /app/data/app.db

# 3. Scale down, update image, scale up
oc scale deployment/backend --replicas=0
# wait for the pod to terminate
oc set image deployment/backend backend=<new-image-tag>
oc scale deployment/backend --replicas=1
# wait for the new pod to be ready

# 4. Verify the app is serving
curl https://<prod-host>/api/health
# → {"status":"ok"}

# 5. Unfreeze.
```

---

## Migration deploy

Use this flow when the PR contains new files in
`backend/migrations/versions/`. The deploy will apply pending migrations
automatically during lifespan via `run_migrations()`.

### Before deploying — test the full chain locally

Test the migration chain against a copy of prod data first. Always.

```bash
# 1. Pull prod DB down
oc exec <pod> -- /app/scripts/backup_db.sh /app/data/app.db
oc cp <pod>:/app/data/app.db.backup.<timestamp> ./prod-snapshot.db

# 2. Point your local backend at the snapshot and run it
DATABASE_URL="sqlite:////absolute/path/to/prod-snapshot.db" \
  python -m app.main

# 3. Watch the lifespan logs. Expected output:
#    [alembic_auto_stamp] existing pre-alembic DB detected, stamping baseline=...
#                                                              (only first time!)
#    Running upgrade <baseline> -> <next-revision>
#    Running upgrade <next-revision> -> <next-next-revision>
#    ...

# 4. Once lifespan completes, verify the schema
./scripts/verify_db.sh /absolute/path/to/prod-snapshot.db

# 5. Exercise the changed code paths end-to-end against the migrated snapshot.
```

If that all looks clean, proceed to the actual deploy.

### The actual prod deploy

```bash
# 1. Freeze prod (announce maintenance, optionally close the route)

# 2. Backup — twice (once on the PV, once pulled down)
oc exec <pod> -- /app/scripts/backup_db.sh /app/data/app.db
oc cp <pod>:/app/data/app.db.backup.<timestamp> ./prod-backup-<timestamp>.db

# 3. Scale down, deploy new image, scale up
oc scale deployment/backend --replicas=0
oc set image deployment/backend backend=<new-image-tag>
oc scale deployment/backend --replicas=1

# 4. Watch the lifespan logs
oc logs -f deployment/backend
# Look for:
#   [alembic_auto_stamp]                       (only on the very first
#                                               migration-aware deploy)
#   Running upgrade <X> -> <Y>                 (one line per migration)
#   Application startup complete.              (or whatever uvicorn logs)

# 5. Verify schema
oc exec <new-pod> -- /app/scripts/verify_db.sh /app/data/app.db

# 6. Smoke-test the app
curl https://<prod-host>/api/health
# plus whatever feature-level checks make sense for this release

# 7. Unfreeze.
```

### What "Running upgrade X -> Y" actually means

When the new pod boots, `run_migrations()` calls `alembic upgrade head`.
Alembic walks the migration chain from the DB's current revision to the
latest. For each pending migration it:

1. Logs `Running upgrade <prev> -> <this>`.
2. Opens a transaction.
3. Executes the `upgrade()` function (the ALTER TABLE etc.).
4. Updates the `alembic_version` table to point at the new revision.
5. Commits the transaction.

If step 3 or 4 fails, the transaction rolls back. `alembic_version` is
left pointing at the previous revision, and the app boot fails (lifespan
raises).

If you have 5 pending migrations and the 3rd fails, migrations 1 and 2
are committed, 3 is rolled back, 4 and 5 don't run. Don't try to manually
fix forward in prod — walk back to the backup.

---

## Migration verification

`backend/scripts/verify_db.sh` runs a few sanity queries. Run it after
any migration deploy.

### Locally

```bash
cd backend
./scripts/verify_db.sh data/app.db
```

### In prod

```bash
oc exec <pod> -- /app/scripts/verify_db.sh /app/data/app.db
```

Expected output (will grow as schema grows):

```
=== alembic current ===
<latest_revision_hash> (head)

=== sub_agent_templates schema ===
CREATE TABLE sub_agent_templates (
  ...
  always_load BOOLEAN NOT NULL DEFAULT 0,
  ...
);

=== row counts ===
sub_agent_templates|<n>
chat_sessions|<n>
messages|<n>
...

=== sub_agent_templates always_load values ===
<rows...>
```

When you add columns or tables in future migrations, extend
`verify_db.sh` with the new assertions. It's faster to extend the script
once than to remember the right ad-hoc queries every deploy.

---

## Recovery — full rollback (data + schema)

Use this when a migration deploy goes badly and you want to put
everything back to the way it was 10 minutes ago.

You always restore the **backup file** AND revert the **deployment image**
together — they're a pair. The old image expects the old schema; if you
keep the new image but restore the old DB, the new code will crash on
missing columns.

### In prod

```bash
# 1. Scale down the broken deployment
oc scale deployment/backend --replicas=0

# 2. Restore the backup
oc cp ./prod-backup-<timestamp>.db <some-pod>:/app/data/app.db.new
oc exec <some-pod> -- mv /app/data/app.db.new /app/data/app.db

# 3. Revert the deployment image to the previous tag
oc set image deployment/backend backend=<previous-image-tag>

# 4. Scale up
oc scale deployment/backend --replicas=1

# 5. Verify the previous image is running and the data is intact
oc exec <new-pod> -- /app/scripts/verify_db.sh /app/data/app.db
curl https://<prod-host>/api/health
```

### Locally

```bash
# Stop the backend
Ctrl+C

# Restore the backup
cp data/app.db.backup.<timestamp> data/app.db

# Check out the previous code
git checkout <previous-branch-or-tag>

# Start the backend
python run.py
```

---

## Recovery — restoring data without rolling back the schema

Sometimes you want a backup's data but the **current** schema — for
example, recovering from user-data corruption a few days after a
successful migration deploy, where you want to keep the new feature but
revert specific data to an earlier state.

You don't need a special procedure. Alembic handles it naturally: the
backup file gets restored, lifespan boots, `run_migrations()` walks it
forward through every migration that's been deployed since the backup
was taken. Data is preserved; new columns get their default values for
the rows from the backup.

### In prod

```bash
# 1. Stop the backend
oc scale deployment/backend --replicas=0

# 2. Replace the live DB with the backup
oc cp ./old-backup.db <some-pod>:/app/data/app.db.new
oc exec <some-pod> -- mv /app/data/app.db.new /app/data/app.db

# 3. Bring the backend up on the CURRENT image (do not revert the image)
oc scale deployment/backend --replicas=1

# 4. Watch the lifespan logs — alembic walks the backup forward to head:
oc logs -f deployment/backend
#   "Running upgrade <X> -> <Y>"
#   "Running upgrade <Y> -> <Z>"
#   ...

# 5. Verify the schema is at head and data from the backup is present
oc exec <pod> -- /app/scripts/verify_db.sh /app/data/app.db
```

### Locally

```bash
# Stop the backend
Ctrl+C

# Restore the older backup
cp data/app.db.backup.<old-timestamp> data/app.db

# Start the backend — it will run migrations on boot
python run.py

# Or run migrations explicitly first
./scripts/migrate.sh
python run.py
```

### What this does NOT do

- It does **not** preserve any data written under the new schema between
  the backup time and the restore. Those rows are gone — that's the
  point of restoring a backup.
- It does **not** populate new columns with anything other than their
  declared defaults. If the migration added an `always_load BOOLEAN
  DEFAULT 0` column, every restored row gets `always_load = 0`, even if
  you'd manually set some to 1 in the live DB before the restore.

If you need a more surgical recovery (preserve some post-migration data,
restore other rows from backup), don't try to improvise it during a
maintenance window. Use the standard full-rollback flow to get back to a
known-good state, then plan the surgery offline.

---

## Background — what restore actually does

The backup file is a byte-for-byte copy of the SQLite database at the
moment the backup ran. When you "restore" by copying it over the live
`app.db`, you're rewinding the entire database:

- All table schemas (DDL)
- All row data
- All indices and constraints
- The `alembic_version` row (if it existed at backup time)

That's why recovery procedures explicitly pair the DB restore with a
deployment image choice:

| You restored... | And deployed... | Result |
|---|---|---|
| Old backup | Old image | Consistent state — like the deploy never happened (full rollback) |
| Old backup | Current image | Alembic walks the backup forward to current head — data restored, schema kept (selective data recovery) |
| Old backup | New (different) image | Don't do this without thinking. If the new image expects schema not in the chain, lifespan will fail. |
| Current DB (no restore) | Old image | Old code may silently mis-handle rows written under the new schema. Don't do this. |

The "current image, old backup" path is the one most people don't
realize is available — it's covered in the "Restoring data without
rolling back the schema" section above. It's not a special procedure;
it's just alembic doing its normal job against a non-current DB file.

---

## Pre-alembic — one-time situation

This applies only the very FIRST time we deploy the alembic-enabled
image to prod. After that, this section is historical.

Before alembic was adopted, the DB schema was created by
`SQLModel.metadata.create_all()` plus ad-hoc `_ensure_*` functions in
`app/database.py`. There was no `alembic_version` table.

On the first deploy of the alembic-aware image, `run_migrations()`
auto-detects this and:

1. Creates the `alembic_version` table.
2. Records the baseline revision as "already applied" — without running it.
3. Walks forward through any new migrations after the baseline.

You'll see this log line exactly once:

```
[alembic_auto_stamp] existing pre-alembic DB detected, stamping baseline=<hash>
```

If you see it on a subsequent deploy, something's wrong — the
`alembic_version` table got dropped between deploys. Investigate before
proceeding.

---

## Authoring migrations

For developers writing new schema changes:

```bash
# 1. Edit the model in backend/app/models/
# 2. Generate a migration
cd backend && source .venv/bin/activate
alembic revision --autogenerate -m "describe the change"

# 3. Inspect the generated file in backend/migrations/versions/
#    Edit if autogen missed anything (indices, server_defaults, etc.).
#    NOT NULL columns added to existing populated tables MUST have a
#    server_default — otherwise the ALTER will fail.

# 4. Test it locally. Same scripts as the "Test a migration locally"
#    section above — backup first, then apply/downgrade/re-apply to
#    confirm the round-trip works against your own data.
./scripts/backup_db.sh data/app.db
./scripts/migrate.sh                     # apply
alembic downgrade -1                     # verify rollback works
./scripts/migrate.sh                     # re-apply
./scripts/verify_db.sh data/app.db       # confirm schema at head

# 5. Commit the migration file alongside the model change.
```

**Once merged to main, a migration is append-only.** Don't edit it,
don't reorder it, don't squash it into another. If it's wrong, write a
follow-up migration that fixes the issue.

If two PRs author migrations off the same base simultaneously, alembic
sees multiple heads and refuses to upgrade. Resolve by editing the
second migration's `down_revision` to point at the first migration's
revision id (a linear rebase) before merging.

---

## Agent template deploys

Sub-agent templates (the JSON files in `app/agents/templates/`) are **bootstrap seeds**, NOT auto-deployed config. A fresh install (empty `sub_agent_templates` table) loads them all at boot. Every subsequent boot ignores file changes — the DB is the sole source of truth from that point on.

To deploy a content change for a seed template (regulated or otherwise) you make a **deliberate** call to the admin import endpoint after the new image is up.

### Why it's deliberate, not automatic

The old behaviour re-synced files into the DB on every boot if hashes differed. That auto-clobbered any UI edits and gave regulated content a "no-deploy-step" that compliance disliked. The current model trades a small amount of friction (one curl per file) for:

- An explicit, audit-logged deploy event for every regulated agent content change.
- No silent overwrite of business-user UI authoring.
- A clear single source of truth (the DB) after install.

### Worked example — update a regulated agent's content

Scenario: `transfer_money_chat.json` got a prompt fix in a merged PR. The new image ships with the updated file. You now want it applied to prod.

```bash
# 1. Verify the file is present in the new image
oc exec <pod> -- cat /app/app/agents/templates/transfer_money_chat.json | head -20

# 2. Apply it. The admin endpoint reads the file, validates it via
#    template_loader, overwrites the DB row (even if it's locked),
#    audit-logs the call, and refreshes the in-memory registry.
oc exec <pod> -- curl -s -X POST \
  -H "X-User-Id: $(whoami)" \
  http://localhost:8000/api/agents/admin/import-file/transfer_money_chat.json

# → {"name":"transfer_money_chat","source":"seed","status":"deployed","hash":"<12-char>"}

# 3. Confirm via the public detail endpoint.
oc exec <pod> -- curl -s http://localhost:8000/api/agents/transfer_money/chat | jq '.hash,.status,.source,.updated_at'
```

For bulk operations, use the helper script `backend/scripts/import_seed.sh`:

```bash
# Plan (read-only) — show every file with its current DB hash, no writes.
./scripts/import_seed.sh --diff

# Apply every *.json in app/agents/templates/.
./scripts/import_seed.sh

# Apply one specific file.
./scripts/import_seed.sh transfer_money_chat.json

# In a pod (the in-pod port is 8000, not 6000):
HOST=http://localhost:8000 ./scripts/import_seed.sh --diff
HOST=http://localhost:8000 ACTOR=$(whoami)@laptop ./scripts/import_seed.sh
```

The script reports `applied=N  unchanged=N  failed=N` per run and exits non-zero if anything failed. Files whose current DB hash already matches the file are reported as `unchanged` and skipped quietly (the API call still goes through, but the row's content doesn't actually change).

### Local equivalent (single file via raw curl)

```bash
curl -s -X POST \
  -H "X-User-Id: $(whoami)" \
  http://localhost:6000/api/agents/admin/import-file/transfer_money_chat.json
```

The local backend port is 6000; in prod it's 8000 inside the pod (use `oc port-forward` if you need to hit it from your laptop).

### What gets overwritten

- **Always**: `graph_definition` (nodes/edges), `description`, `search_hint`, `always_load`, channel + locked + suspend flags, schema_version, and `hash`. The `updated_at` timestamp moves.
- **Stamped to `'seed'`**: the `source` column, regardless of its previous value. The admin import is asserting "this file is now the truth for this row."
- **Preserved**: `status` stays whatever it was; `created_by` and `created_at` are not touched.

If a row was previously edited through the Builder UI and is now `source='user'`, the admin import WILL clobber those UI edits and flip source back to `'seed'`. That's the intended escape hatch — an admin can always reset a row to canonical file content — but **don't surprise a business-user team by doing it without a heads-up**.

### What if I deleted a regulated row by accident?

The admin import endpoint inserts a fresh row when the row is missing:

```bash
oc exec <pod> -- curl -s -X POST \
  http://localhost:8000/api/agents/admin/import-file/transfer_money_chat.json
# → {"name":"transfer_money_chat",...}  ← row re-created
```

If you deleted the row AND the file is gone (or wrong) in the image, restore from a DB backup using the standard restore procedure above. The DB row is canonical at all times after bootstrap.

### Bootstrap edge case — fresh OpenShift install

On the very first deploy to an empty environment, `bootstrap_from_files()` runs at lifespan startup and inserts every JSON from the image into the empty DB with `source='seed'`. No admin call needed for the initial install.

Verify the bootstrap ran:

```bash
oc logs deployment/backend | grep -E "template_bootstrap_(inserted|skipped)"
# → [template_bootstrap_inserted] name=transfer_money_chat file=transfer_money_chat.json
# → [template_bootstrap_inserted] name=card_advisor_chat file=card_advisor.chat.json
# → ...
```

On every subsequent boot:

```bash
oc logs deployment/backend | grep template_bootstrap
# → [template_bootstrap_skipped] reason=db_non_empty existing=transfer_money_chat
```

If you ever see `[template_bootstrap_inserted]` on a non-first boot, something nuked the table — investigate before doing anything else, because all UI-authored agents are gone too.

### Agent template deploy — checklist

- [ ] Image deployed with the new JSON files baked in.
- [ ] Run `./scripts/import_seed.sh --diff` to preview the plan and double-check which rows are about to change.
- [ ] Run `./scripts/import_seed.sh` (or `./scripts/import_seed.sh <filename>` for a single file). Confirm `failed=0` and the expected `applied` count.
- [ ] Spot-check via `GET /api/agents/<name>/<channel>` that the hash/timestamp moved.
- [ ] Spot-check via the Builder UI that the agent's graph looks right.
- [ ] If you touched an `always_load=true` agent, do one chat turn to confirm the Planner still binds it without errors.

---

## Quick reference — local vs prod commands

| Operation | Locally | In prod (OpenShift) |
|---|---|---|
| Backup | `./scripts/backup_db.sh data/app.db` | `oc exec <pod> -- /app/scripts/backup_db.sh /app/data/app.db` |
| Copy backup off-system | already on your laptop | `oc cp <pod>:/app/data/app.db.backup.<ts> ./prod-backup.db` |
| Stop backend | `Ctrl+C` | `oc scale deployment/backend --replicas=0` |
| Swap to new code | `git pull` / checkout | `oc set image deployment/backend backend=<new-tag>` |
| Start backend | `python run.py` | `oc scale deployment/backend --replicas=1` |
| Watch logs | already in your terminal | `oc logs -f deployment/backend` |
| Apply migrations manually | `./scripts/migrate.sh` | `oc exec <pod> -- /app/scripts/migrate.sh` |
| Verify schema | `./scripts/verify_db.sh data/app.db` | `oc exec <pod> -- /app/scripts/verify_db.sh /app/data/app.db` |
| Restore backup | `cp data/app.db.backup.<ts> data/app.db` | `oc cp ./backup.db <pod>:/app/data/app.db.new && oc exec <pod> -- mv ...` |
| Apply an agent JSON file change | `./scripts/import_seed.sh <filename>` | `oc exec <pod> -- env HOST=http://localhost:8000 /app/scripts/import_seed.sh <filename>` |
| Apply ALL seed files (bulk) | `./scripts/import_seed.sh` | `oc exec <pod> -- env HOST=http://localhost:8000 /app/scripts/import_seed.sh` |
| Plan seed imports (read-only) | `./scripts/import_seed.sh --diff` | `oc exec <pod> -- env HOST=http://localhost:8000 /app/scripts/import_seed.sh --diff` |
| Inspect a sub-agent's current row | `curl http://localhost:6000/api/agents/<name>/<channel>` | `oc exec <pod> -- curl http://localhost:8000/api/agents/<name>/<channel>` |

The `oc exec <pod> -- <command>` pattern means "run this inside the
container." Locally, you ARE the container — drop the wrapper.

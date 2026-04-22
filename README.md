# Erica Agent Platform

Channel-aware LangGraph orchestrator with a Planner + deterministic Presenter,
sub-agent framework, RAG, and interactive widgets. See
[`backend/docs/architecture.md`](./backend/docs/architecture.md) for the full
orchestrator design and
[`backend/docs/compound_response_plan.md`](./backend/docs/compound_response_plan.md)
for the three-turn-shape response strategy.

## Quickstart

```bash
./setup.sh
```

`setup.sh` is idempotent — re-run any time. It creates `backend/.venv`,
installs backend + frontend dependencies, bootstraps `backend/data/`
(SQLite + ChromaDB + KB descriptor placeholder), and copies
`backend/.env.example` → `backend/.env`.

### One manual step

After the first run, open `backend/.env` and set:

```
OPENAI_API_KEY=sk-...
```

Without a key the backend will start but every LLM call will 401.

### Start the servers

```bash
# terminal 1 — backend
cd backend
source .venv/bin/activate
python run.py        # http://localhost:6000

# terminal 2 — frontend
cd frontend
npm run dev          # http://localhost:6001
```

Visit http://localhost:6001/login and pick a profile (aryash, alexm, chrisp).

## What setup.sh does not do

- **Upload knowledge-base content.** The KB starts empty. Use the
  `/knowledge` page in the UI to upload markdown files; the descriptor at
  `backend/data/kb_descriptor.txt` rewrites itself after each upload.
- **Seed chat sessions.** Every fresh clone starts with an empty chat log.
- **Provision LangSmith.** Tracing is off by default
  (`LANGSMITH_TRACING=false` in `.env`). Flip it on with a self-hosted URL
  and API key when you want observability — see
  `backend/docs/architecture.md` §Observability.

## Directory layout

```
backend/
  app/            # FastAPI app, LangGraph orchestrator, sub-agent framework
  api_data/       # Mock bank API responses (committed — transfer/refund flows)
  profile/        # Mock profile + transaction JSON (committed)
  scripts/        # bootstrap.py, eval_planner_routing.py
  data/           # SQLite + Chroma runtime state  (gitignored, created by setup)
  uploads/        # User-uploaded KB markdown        (gitignored)
  .env            # OPENAI_API_KEY + settings         (gitignored)
frontend/
  src/            # React app (Vite + Tailwind)
  tests/          # Playwright E2E specs
setup.sh          # one-shot bootstrapper
```

## Running tests

```bash
# backend unit + integration
cd backend && source .venv/bin/activate
python -m pytest tests/

# Planner-routing eval (requires OPENAI_API_KEY)
python scripts/eval_planner_routing.py

# frontend E2E (requires Playwright install — see frontend/tests/*.spec.js)
cd frontend
npm install --save-dev @playwright/test
npx playwright install chromium
npm run test:e2e
```

## Re-running setup

`./setup.sh` is safe to re-run. `bootstrap.py` is idempotent:

- SQLite — uses `CREATE TABLE IF NOT EXISTS`; existing rows are preserved.
- Chroma — `get_or_create_collection`; indexes survive.
- KB descriptor — only written if missing.

To **wipe and start over**: `rm -rf backend/data backend/uploads` then re-run
`./setup.sh`. Deleting `backend/data/` removes chat history, memory facts,
widget instances, and sub-agent templates (which re-seed from
`backend/app/agents/templates/*.json` on the next backend boot).

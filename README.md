# Erica Agent Platform

Channel-aware LangGraph orchestrator with a **Planner + deterministic
Presenter**, channel-specific sub-agents (Transfer, Refund), RAG-backed
knowledge search, and interactive widgets. FastAPI backend + Vite/React
frontend.

- [`backend/docs/architecture.md`](./backend/docs/architecture.md) — full
  orchestrator design (graph, state, routing, observability).
- [`backend/docs/compound_response_plan.md`](./backend/docs/compound_response_plan.md)
  — the three-turn-shape response strategy (fast-path / two-phase /
  no-widget) and the hop-guard safety valve.
- [`backend/docs/widgets.md`](./backend/docs/widgets.md) — widget catalog
  and the 4-rule Presenter engine.
- [`backend/docs/transfer_flow.md`](./backend/docs/transfer_flow.md) — the
  Transfer sub-agent walkthrough (applies to Refund by analogy).

## Prerequisites

| | Minimum | Check |
|---|---|---|
| Python | 3.11+ (tested on 3.13) | `python3 --version` |
| Node.js | 18+ | `node --version` |
| npm | 9+ | `npm --version` |
| OpenAI API key | any tier with gpt-5 access | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |

Bash on Linux or macOS. Windows users run `setup.sh` under WSL or run
the equivalent commands manually (see [Manual setup](#manual-setup)).

## Quickstart

```bash
git clone git@github.com:emad-noorizadeh/finchat.git
cd finchat
./setup.sh
```

`setup.sh` is idempotent — safe to re-run any time. It:

1. Creates `backend/.venv` (Python 3 venv).
2. Installs `backend/requirements.txt`.
3. Copies `backend/.env.example` → `backend/.env` (if missing).
4. Runs `backend/scripts/bootstrap.py`:
   - creates `backend/data/` with fresh SQLite (`app.db`, `checkpoints.db`),
   - creates the Chroma `system_knowledge` collection,
   - writes a placeholder `kb_descriptor.txt`.
5. Runs `npm install` in `frontend/`.

### One manual step — add your OpenAI key

Open `backend/.env` and set:

```dotenv
OPENAI_API_KEY=sk-...
```

Without it the backend starts but every LLM call will 401.

### Start the servers

Two terminals:

```bash
# terminal 1 — backend  (http://localhost:6000)
cd backend
source .venv/bin/activate
python run.py

# terminal 2 — frontend (http://localhost:6001)
cd frontend
npm run dev
```

Open http://localhost:6001/login and pick one of the mock profiles:
`aryash`, `alexm`, or `chrisp`.

## Environment variables

All live in `backend/.env` (SQLite, Chroma, and upload paths are derived
from the backend layout — no need to set them). The example file
`backend/.env.example` ships with the required shape; defaults below are
the production values.

### Required

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI secret key. Used for both the LLM and embeddings. |

### Core settings (defaults work out of the box)

| Variable | Default | Purpose |
|---|---|---|
| `APP_NAME` | `AI Agent Chat Platform` | Label shown in logs. |
| `DEBUG` | `false` | Extra SQL/log verbosity when `true`. |
| `LLM_MODEL` | `gpt-5` | Primary Planner LLM. |
| `LLM_REASONING_EFFORT` | `low` | For gpt-5 / o-family models: `minimal` \| `low` \| `medium` \| `high`. Tradeoff: `low` is ~3–5× faster than `medium`. |
| `EMBEDDING_MODEL` | `text-embedding-3-large` | Used for KB + memory embeddings. |
| `CORS_ORIGINS` | `["http://localhost:6001"]` | JSON array. Add more origins if you serve the frontend elsewhere. |

### LangSmith observability (optional)

Traces stay local when off. See
[`architecture.md` §Observability](./backend/docs/architecture.md#observability-langsmith)
for the full story.

| Variable | Default | Purpose |
|---|---|---|
| `LANGSMITH_TRACING` | `false` | Master on/off. `false` = zero bytes leave the process. |
| `LANGSMITH_API_KEY` | *(empty)* | Required when tracing is on. |
| `LANGSMITH_ENDPOINT` | *(empty → public cloud)* | Company self-hosted URL, e.g. `https://langsmith.my-company.internal/api/v1`. |
| `LANGSMITH_PROJECT` | `finchat` | Project name on the dashboard. |
| `LANGSMITH_HIDE_INPUTS` | `false` | Redact trace inputs (recommended for prod with PII). |
| `LANGSMITH_HIDE_OUTPUTS` | `false` | Redact trace outputs. |

### Frontend-side overrides

`frontend/.env` (or `.env.local`):

| Variable | Default | Purpose |
|---|---|---|
| `VITE_PORT` | `6001` | Frontend dev server port. |
| `VITE_API_URL` | `http://localhost:6000` | Backend target for the `/api/*` proxy. |

## What setup.sh does *not* do

- **Upload knowledge-base content.** The KB starts empty. Upload
  markdown files via the `/knowledge` page in the UI; the descriptor at
  `backend/data/kb_descriptor.txt` rewrites itself after each upload.
- **Seed chat sessions.** Every fresh clone starts with an empty chat log.
- **Provision LangSmith.** Off by default. Set the five `LANGSMITH_*`
  variables in `backend/.env` when you want traces.
- **Log you in.** Pick a profile on the `/login` page; profiles are
  in-memory mocks (`aryash`, `alexm`, `chrisp`) backed by the JSON in
  `backend/profile/` and `backend/api_data/`.

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
  .env.example    # committed template
  .venv/          # Python venv                      (gitignored)
frontend/
  src/            # React app (Vite + Tailwind)
  tests/          # Playwright E2E specs
  node_modules/   # npm deps                         (gitignored)
setup.sh          # one-shot bootstrapper
README.md
```

## Running tests

```bash
# Backend unit + integration
cd backend && source .venv/bin/activate
python -m pytest tests/

# Planner-routing eval (requires OPENAI_API_KEY)
python scripts/eval_planner_routing.py

# Live OpenAI streaming-order sanity check (requires OPENAI_API_KEY; slow)
python -m pytest tests/test_streaming_order.py

# Frontend E2E — Playwright scaffold (needs one-time install)
cd frontend
npm install --save-dev @playwright/test
npx playwright install chromium
npm run test:e2e
```

## Manual setup

If `setup.sh` can't run on your OS (e.g., bare Windows without WSL), do
each step by hand:

```bash
# Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env and set OPENAI_API_KEY
python scripts/bootstrap.py

# Frontend
cd ../frontend
npm install
```

## Re-running setup / wiping state

`./setup.sh` is safe to re-run. `bootstrap.py` is idempotent:

- **SQLite** uses `CREATE TABLE IF NOT EXISTS`; existing rows survive.
- **Chroma** uses `get_or_create_collection`; indexed embeddings survive.
- **KB descriptor** is only written when missing (regenerated by the
  indexing service after each upload/delete in the UI).

To **wipe everything and start fresh**:

```bash
rm -rf backend/data backend/uploads
./setup.sh
```

Deleting `backend/data/` removes chat history, memory facts, widget
instances, uploaded KB content, and sub-agent templates. Templates
re-seed from `backend/app/agents/templates/*.json` on the next backend
boot.

## Troubleshooting

### Port 6000 or 6001 already in use

- **Backend:** edit `backend/run.py` and change `port=6000`. Also set
  `VITE_API_URL=http://localhost:<new-port>` in `frontend/.env`.
- **Frontend:** set `VITE_PORT=<new-port>` in `frontend/.env` (or
  `frontend/.env.local`) and update `CORS_ORIGINS` in `backend/.env` to
  match.

### `pip install` prompts for a username / stalls

A private package index is configured in your local `pip.conf`. Bypass
for this install:

```bash
PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple \
  pip install -r backend/requirements.txt
```

### Every LLM call returns 401

`OPENAI_API_KEY` isn't set in `backend/.env`, or the backend was running
before you edited the file and hasn't reloaded. Kill `python run.py`
and start it again.

### Backend says "Profile data not loaded"

The in-memory profile cache was cold after a restart. `enrich()`
auto-rehydrates on the next turn; if it still fails, check
`backend/profile/<user>_profile.json` exists for the logged-in user.

### Widget says "Unknown widget"

Frontend is running an older build that doesn't know about a newly added
widget type. Rebuild (`npm run dev` picks up HMR automatically) or hard
reload the browser tab.

### `[hop_guard_triggered]` in backend logs

The Planner did two rounds of tool-gathering without handing off to the
Presenter. Expected when a prompt change is still being tuned; see
[`compound_response_plan.md`](./backend/docs/compound_response_plan.md)
for what the flag means and how to debug.

## Architecture at a glance

```
  user →  FastAPI SSE  →  LangGraph orchestrator
                           ├─ Planner LLM (gpt-5)
                           ├─ tool_execute (parallel tool calls)
                           ├─ Presenter (deterministic rules engine)
                           └─ Sub-agents (Transfer, Refund)
                        ←  streamed events (prose / widget / activity)
  Chroma ← knowledge_search
  SQLite ← chat, memory, widget instances, sub-agent templates
```

See [`backend/docs/architecture.md`](./backend/docs/architecture.md) for
the full graph shape, state schema, observability, and sub-agent
framework.

## License

Proprietary — internal project.

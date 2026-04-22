#!/usr/bin/env bash
# Erica Agent Platform — fresh-clone setup.
#
# What this does:
#   1. Creates backend/.venv if missing.
#   2. Installs backend/requirements.txt.
#   3. Copies backend/.env.example → backend/.env if missing.
#   4. Runs backend/scripts/bootstrap.py (SQLite + Chroma init, empty descriptor).
#   5. Installs frontend npm deps.
#
# Knowledge base content is NOT seeded — upload via the /knowledge UI
# after the app is running.
#
# Safe to re-run. Each step is idempotent.
#
# Prereqs:
#   - Python 3.11+ on PATH
#   - Node 18+ on PATH
#   - An OpenAI API key (you'll be prompted to add it to backend/.env)
#
# Usage:
#   ./setup.sh

set -eu

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$REPO_ROOT/backend"
FRONTEND="$REPO_ROOT/frontend"

say()  { printf "\n\033[1m==> %s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*" >&2; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$*" >&2; exit 1; }

# --- 0. Prereq checks ---
say "Checking prerequisites"
command -v python3 >/dev/null 2>&1 || fail "python3 not found on PATH"
command -v node    >/dev/null 2>&1 || fail "node not found on PATH"
command -v npm     >/dev/null 2>&1 || fail "npm not found on PATH"
py_ver=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
node_ver=$(node --version)
ok "python3 $py_ver"
ok "node $node_ver"

# --- 1. Backend venv + deps ---
say "Setting up backend venv"
if [ ! -d "$BACKEND/.venv" ]; then
  python3 -m venv "$BACKEND/.venv"
  ok "created backend/.venv"
else
  ok "backend/.venv already exists"
fi

# shellcheck disable=SC1091
source "$BACKEND/.venv/bin/activate"
python -m pip install --quiet --upgrade pip
pip install --quiet -r "$BACKEND/requirements.txt"
ok "backend dependencies installed"

# --- 2. .env bootstrap ---
say "Setting up backend/.env"
if [ ! -f "$BACKEND/.env" ]; then
  if [ -f "$BACKEND/.env.example" ]; then
    cp "$BACKEND/.env.example" "$BACKEND/.env"
    ok "created backend/.env from .env.example"
    warn "Edit backend/.env and set OPENAI_API_KEY before starting the backend."
  else
    fail "backend/.env.example missing — cannot bootstrap env"
  fi
else
  ok "backend/.env already exists"
fi

# --- 3. Run the Python bootstrapper ---
say "Bootstrapping databases"
cd "$BACKEND"
python scripts/bootstrap.py
cd "$REPO_ROOT"

# --- 4. Frontend deps ---
say "Installing frontend dependencies"
if [ -d "$FRONTEND/node_modules" ]; then
  ok "frontend/node_modules already exists — running install to sync"
fi
( cd "$FRONTEND" && npm install --silent )
ok "frontend dependencies installed"

# --- 5. Done ---
say "Setup complete"
cat <<EOF

Start the backend:
  cd backend
  source .venv/bin/activate
  python run.py        # serves on http://localhost:6000

Start the frontend (in a second terminal):
  cd frontend
  npm run dev          # serves on http://localhost:6001

First-run notes:
  - Make sure backend/.env has your OPENAI_API_KEY before starting the
    backend — the LLM and embeddings calls will 401 without it.
  - Knowledge base starts empty. Upload markdown files via the /knowledge
    page in the UI; the KB descriptor rewrites itself after each upload.
  - Regulated sub-agents (transfer_money, refund_fee) seed themselves
    from app/agents/templates/*.json on first backend boot.
  - Mock bank profiles + transfer/refund data are in backend/profile/ and
    backend/api_data/ (committed, no action needed).

EOF

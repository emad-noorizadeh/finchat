#!/usr/bin/env bash
# Apply pending alembic migrations against the configured database.
# Used by devs after pulling main, and by ops as a manual escape hatch
# if lifespan-driven migration fails and we need to debug.
#
# Usage:
#   ./scripts/migrate.sh
set -eu
cd "$(dirname "$0")/.."
# Activate venv if present (local dev). In containers, alembic is on PATH already.
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
exec alembic upgrade head

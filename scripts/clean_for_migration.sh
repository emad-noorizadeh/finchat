#!/usr/bin/env bash
# Strip out everything that gets recreated by `python -m venv` + `pip install`
# + `npm install`. Run before tarring / rsyncing the project to a new server.
#
# Usage:
#   scripts/clean_for_migration.sh             # dry-run — shows what would be removed
#   scripts/clean_for_migration.sh --apply     # actually delete
#
# Always preserved (these are runtime DATA, not setup output):
#   backend/.env
#   backend/data/        (sqlite + chroma)
#   backend/uploads/
#   backend/api_data/
#   everything tracked in git
#
# After running on the source machine and copying the project to the target,
# run on the target:
#   cd backend && python3.13 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
#   cd ../frontend && npm install

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

# Each entry: a path or a `find` recipe. Anything resolved here gets removed.
TARGETS=(
  "backend/.venv"
  "frontend/node_modules"
  "frontend/dist"
  "frontend/.vite"
  "backend/.pytest_cache"
  "backend/.mypy_cache"
  "backend/.ruff_cache"
)

# Python caches scattered through the tree. Use newline-delimited strings
# instead of arrays so we run on macOS's default bash 3.2 (no mapfile).
PYCACHE=$(find "$ROOT/backend" -type d -name '__pycache__' 2>/dev/null)
PYC=$(find "$ROOT/backend" -type f \( -name '*.pyc' -o -name '*.pyo' \) 2>/dev/null)
PYCACHE_COUNT=$(printf '%s\n' "$PYCACHE" | grep -c '^/' || true)
PYC_COUNT=$(printf '%s\n' "$PYC" | grep -c '^/' || true)

human() {
  local path="$1"
  [[ -e "$path" ]] || { printf '       —   %s (absent)\n' "$path"; return; }
  local size
  size=$(du -sh "$path" 2>/dev/null | awk '{print $1}')
  printf '  %6s   %s\n' "$size" "$path"
}

echo "=== finchat: clean setup-generated folders ==="
echo
echo "Top-level targets:"
for t in "${TARGETS[@]}"; do
  human "$ROOT/$t"
done
echo
echo "Python caches: $PYCACHE_COUNT __pycache__ dirs, $PYC_COUNT .pyc/.pyo files"
echo

if [[ $APPLY -eq 0 ]]; then
  echo "(dry-run — re-run with --apply to actually delete)"
  exit 0
fi

echo "Deleting…"
for t in "${TARGETS[@]}"; do
  if [[ -e "$ROOT/$t" ]]; then
    rm -rf -- "$ROOT/$t"
    echo "  removed $t"
  fi
done
if [[ -n "$PYCACHE" ]]; then
  printf '%s\n' "$PYCACHE" | while IFS= read -r d; do [[ -n "$d" ]] && rm -rf -- "$d"; done
fi
if [[ -n "$PYC" ]]; then
  printf '%s\n' "$PYC" | while IFS= read -r f; do [[ -n "$f" ]] && rm -f -- "$f"; done
fi
echo "  removed $PYCACHE_COUNT __pycache__ dirs, $PYC_COUNT .pyc/.pyo files"

echo
echo "Done. Project is ready to copy. On the target server, run:"
echo "  cd backend && python3.13 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
echo "  cd ../frontend && nvm use 22 && npm install"

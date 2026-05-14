#!/usr/bin/env bash
# Launch LangGraph Studio against this backend.
#
#   - Activates the project venv.
#   - Hands off to scripts/studio.py, which baked in --allow-blocking
#     and silences the noisy watchfiles change-detection logs.
#   - Forwards any extra args to `langgraph dev` (e.g. --tunnel, --port).
#
# Usage:
#   ./scripts/studio.sh                 # default Studio session
#   ./scripts/studio.sh --tunnel        # public Cloudflare tunnel
#   ./scripts/studio.sh --port 2025     # alternate port
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/.."

# shellcheck disable=SC1091
source .venv/bin/activate

exec python "$HERE/studio.py" "$@"

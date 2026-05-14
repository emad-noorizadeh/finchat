#!/usr/bin/env bash
# Deploy seed-template content changes by calling the admin import endpoint.
# Use this AFTER deploying a new image whose app/agents/templates/ JSON files
# differ from what's in the DB. File changes are NOT auto-applied at boot
# (bootstrap runs only against an empty table). See backend/docs/deploy_runbook.md
# § "Agent template deploys" for the full story.
#
# Usage:
#   ./scripts/import_seed.sh                          # apply all *.json
#   ./scripts/import_seed.sh transfer_money_chat.json # apply one file
#   ./scripts/import_seed.sh --diff                   # plan only, no writes
#   ./scripts/import_seed.sh --diff <filename>        # plan one file
#
# Environment overrides:
#   HOST     Backend base URL. Default http://localhost:6000 locally,
#            override to http://localhost:8000 inside an OpenShift pod
#            or to a remote URL with port-forwarding set up.
#   ACTOR    Audit-log attribution. Default $(whoami). Sent as X-User-Id.
#
# Examples:
#   # Local dev, apply everything
#   ./scripts/import_seed.sh
#
#   # Inside the pod
#   HOST=http://localhost:8000 ./scripts/import_seed.sh --all
#
#   # From your laptop with port-forwarding
#   oc port-forward deployment/backend 8000:8000 &
#   HOST=http://localhost:8000 ACTOR=$(whoami)@laptop ./scripts/import_seed.sh
#
# Exit codes:
#   0  every file applied (or planned) successfully
#   1  one or more files failed; see stderr
#   2  bad arguments

set -eu
cd "$(dirname "$0")/.."

HOST="${HOST:-http://localhost:6000}"
ACTOR="${ACTOR:-$(whoami)}"
TEMPLATE_DIR="app/agents/templates"

DIFF_MODE=0
FILES=()

# Argument parsing.
while [ $# -gt 0 ]; do
  case "$1" in
    --diff)
      DIFF_MODE=1
      shift
      ;;
    --all)
      # explicit form of the default; accept it for clarity in scripts
      shift
      ;;
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    --*)
      echo "unknown flag: $1" >&2
      exit 2
      ;;
    *)
      FILES+=("$1")
      shift
      ;;
  esac
done

# Build the file list — explicit arg, or every *.json in the templates dir.
if [ "${#FILES[@]}" -eq 0 ]; then
  while IFS= read -r f; do
    FILES+=("$(basename "$f")")
  done < <(find "$TEMPLATE_DIR" -maxdepth 1 -name '*.json' | sort)
fi

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "no *.json files found in $TEMPLATE_DIR" >&2
  exit 2
fi

# Sanity-check: the backend is reachable.
if ! curl -sf "${HOST}/api/health" >/dev/null; then
  echo "backend not reachable at ${HOST} — is it running?" >&2
  echo "  hint: set HOST=http://localhost:8000 inside a pod, or use oc port-forward" >&2
  exit 1
fi

# Hash helper: extract the first 12 chars of the loader-canonical hash by
# asking the backend what it currently has stored for a given (name, channel).
# Returns empty string if the row doesn't exist yet.
current_hash() {
  local name="$1" channel="$2"
  curl -sf "${HOST}/api/agents/${name}/${channel}" 2>/dev/null \
    | awk -F'"' '/"hash"[[:space:]]*:/{for(i=1;i<=NF;i++)if($i=="hash"){print $(i+2);exit}}' \
    | cut -c1-12
}

# Parse a template JSON file to extract its (name, channel) so we can probe
# the current DB row without applying. Cheap python one-liner via the venv.
extract_name_channel() {
  local file="$1"
  if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  python3 -c "
import json, sys
raw = json.load(open(sys.argv[1]))
print(raw.get('name', ''))
print(raw.get('channel', 'chat'))
" "$file"
}

failures=0
applied=0
skipped=0

echo "=== seed import ==="
echo "host:   ${HOST}"
echo "actor:  ${ACTOR}"
echo "files:  ${#FILES[@]}"
[ "$DIFF_MODE" -eq 1 ] && echo "mode:   --diff (read-only)"
echo

for filename in "${FILES[@]}"; do
  file_path="${TEMPLATE_DIR}/${filename}"
  if [ ! -f "$file_path" ]; then
    echo "  ✗ ${filename} — file not found in ${TEMPLATE_DIR}" >&2
    failures=$((failures + 1))
    continue
  fi

  # Plan: read the file's (name, channel) and compare to what's in the DB.
  # Use newline-delimited capture for portability (macOS bash 3 lacks mapfile).
  info_raw=$(extract_name_channel "$file_path") || {
    echo "  ✗ ${filename} — cannot parse JSON" >&2
    failures=$((failures + 1))
    continue
  }
  tpl_name=$(printf '%s\n' "$info_raw" | sed -n '1p')
  channel=$(printf '%s\n' "$info_raw" | sed -n '2p')
  channel="${channel:-chat}"

  # Derive the agent_name (the URL segment for the detail endpoint) from
  # the template by going through the listing API — easier than re-parsing.
  agent_name=$(curl -sf "${HOST}/api/agents" \
    | awk -v t="$tpl_name" '
        BEGIN{RS="{"} $0 ~ "\"template_name\":[[:space:]]*\""t"\"" {
          if (match($0,/"name":[[:space:]]*"[^"]*"/)) {
            s=substr($0,RSTART,RLENGTH); gsub(/.*"name":[[:space:]]*"/,"",s);
            gsub(/".*/,"",s); print s; exit
          }
        }')
  agent_name="${agent_name:-$tpl_name}"

  before_hash=$(current_hash "$agent_name" "$channel")
  before_label="${before_hash:-<new>}"

  if [ "$DIFF_MODE" -eq 1 ]; then
    printf "  • %-40s  template=%s  current_hash=%s\n" \
      "$filename" "$tpl_name" "$before_label"
    continue
  fi

  resp=$(curl -sf -X POST \
    -H "X-User-Id: ${ACTOR}" \
    "${HOST}/api/agents/admin/import-file/${filename}" 2>&1) || {
      echo "  ✗ ${filename} — admin import failed: ${resp}" >&2
      failures=$((failures + 1))
      continue
  }

  # Pull the post-import hash out of the response.
  after_hash=$(echo "$resp" \
    | awk -F'"' '/"hash"[[:space:]]*:/{for(i=1;i<=NF;i++)if($i=="hash"){print $(i+2);exit}}' \
    | cut -c1-12)
  after_label="${after_hash:-<unknown>}"

  if [ "$before_hash" = "$after_hash" ] && [ -n "$before_hash" ]; then
    printf "  · %-40s  hash=%s  (no change)\n" "$filename" "$after_label"
    skipped=$((skipped + 1))
  else
    printf "  ✓ %-40s  %s → %s\n" "$filename" "$before_label" "$after_label"
    applied=$((applied + 1))
  fi
done

echo
if [ "$DIFF_MODE" -eq 1 ]; then
  echo "planned ${#FILES[@]} file(s); no changes written"
else
  echo "applied=${applied}  unchanged=${skipped}  failed=${failures}  total=${#FILES[@]}"
fi

[ "$failures" -gt 0 ] && exit 1
exit 0

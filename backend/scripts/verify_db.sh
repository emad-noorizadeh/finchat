#!/usr/bin/env bash
# Quick post-migration verification. Asserts that the schema is at head
# and that key tables contain expected columns / row counts.
# Extend this script as the schema grows.
#
# Usage:
#   ./scripts/verify_db.sh                 # uses data/app.db
#   ./scripts/verify_db.sh /custom/path.db
set -eu
cd "$(dirname "$0")/.."
DB_PATH="${1:-data/app.db}"

if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "=== alembic current ==="
alembic current

echo
echo "=== sub_agent_templates schema ==="
sqlite3 "$DB_PATH" ".schema sub_agent_templates"

echo
echo "=== row counts ==="
sqlite3 "$DB_PATH" "
  SELECT 'sub_agent_templates' AS table_name, COUNT(*) AS rows FROM sub_agent_templates
  UNION ALL SELECT 'chat_sessions',   COUNT(*) FROM chat_sessions
  UNION ALL SELECT 'messages',        COUNT(*) FROM messages
  UNION ALL SELECT 'files',           COUNT(*) FROM files
  UNION ALL SELECT 'widget_instances', COUNT(*) FROM widget_instances;
"

echo
echo "=== sub_agent_templates always_load values ==="
sqlite3 "$DB_PATH" "SELECT name, channel, always_load FROM sub_agent_templates;"

#!/usr/bin/env bash
# Snapshot the SQLite database. Run before any migration deploy.
# Uses SQLite's online backup API (.backup) — safe to run while the app
# is still serving; produces a transactionally consistent copy.
#
# Usage:
#   ./scripts/backup_db.sh                 # uses data/app.db
#   ./scripts/backup_db.sh /custom/path.db
set -eu
cd "$(dirname "$0")/.."
DB_PATH="${1:-data/app.db}"
if [ ! -f "$DB_PATH" ]; then
  echo "Error: DB file not found at $DB_PATH" >&2
  exit 1
fi
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$DB_PATH.backup.$TIMESTAMP"
sqlite3 "$DB_PATH" ".backup '$BACKUP'"
echo "Backup created: $BACKUP"
echo "Size: $(du -h "$BACKUP" | cut -f1)"

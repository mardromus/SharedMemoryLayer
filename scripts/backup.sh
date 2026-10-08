#!/usr/bin/env bash
# Back up the production database (docker-compose.prod.yml).
#
#   scripts/backup.sh [directory]        default directory: backups/
#
# PostgreSQL is the source of truth: memories, versions, users, agents, API-key
# hashes and the audit log. The Qdrant vector index is derived from it and is
# rebuilt after a restore (POST /api/admin/reindex?recreate=true, or
# "Rebuild vector index" on the dashboard). Keep a copy of .env separately and
# privately - it holds the secrets.
#
# All dumps are kept by default. KEEP=<n> deletes all but the n newest
# shared_memory-*.dump files in the directory.
# Daily at 03:00 via cron:  0 3 * * * cd /path/to/SharedMemoryLayer && KEEP=14 scripts/backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

DIR=${1:-backups}
KEEP=${KEEP:-0}
mkdir -p "$DIR"
FILE="$DIR/shared_memory-$(date -u +%Y%m%dT%H%M%SZ).dump"

docker compose -f docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$FILE.partial"
mv "$FILE.partial" "$FILE"
chmod 600 "$FILE"
echo "Wrote $FILE ($(du -h "$FILE" | cut -f1))"

if [ "$KEEP" -gt 0 ]; then
  ls -1t "$DIR"/shared_memory-*.dump | tail -n +"$((KEEP + 1))" | while read -r old; do
    rm -f -- "$old" && echo "Removed old backup $old"
  done
fi

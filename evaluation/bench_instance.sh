#!/bin/sh
# Starts a second API process (same image, same code) on 127.0.0.1:8001 backed by its own
# PostgreSQL database and Qdrant collection. Large public benchmarks (LongMemEval, BEIR) ingest
# 100k+ memories; running them here keeps the demo organisation's data and its append-only audit
# log untouched. Rate limiting is disabled on this instance only, so the benchmark measures
# retrieval, not throttling. Run inside the backend container:
#
#   docker compose exec -d backend sh /opt/evaluation/bench_instance.sh
#   docker compose exec backend python /opt/evaluation/longmemeval_benchmark.py --base-url http://127.0.0.1:8001
#
# Stop it and remove its database and collection afterwards with:
#   docker compose exec backend sh /opt/evaluation/bench_instance.sh --drop
set -e
MAIN_DB="${DATABASE_URL##*/}"
BENCH_DB="${BENCH_DB:-${MAIN_DB}_bench}"
BENCH_COLLECTION="${BENCH_COLLECTION:-bench_memories}"

PIDFILE=/tmp/bench_instance.pid
if [ "${1:-}" = "--drop" ] && [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  rm -f "$PIDFILE"
fi

python - "$BENCH_DB" "$BENCH_COLLECTION" "${1:-}" <<'EOF'
import os, sys
import psycopg
from qdrant_client import QdrantClient

db, collection, action = sys.argv[1], sys.argv[2], sys.argv[3]
url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(url, autocommit=True) as conn:
    exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone()
    if action == "--drop":
        if exists:
            conn.execute(f'DROP DATABASE "{db}" WITH (FORCE)')
        q = QdrantClient(url=os.environ["QDRANT_URL"], api_key=os.environ.get("QDRANT_API_KEY") or None)
        if q.collection_exists(collection):
            q.delete_collection(collection)
        print(f"dropped database {db} and collection {collection}")
        sys.exit(0)
    if not exists:
        conn.execute(f'CREATE DATABASE "{db}"')
        print(f"created database {db}")
EOF
[ "${1:-}" = "--drop" ] && exit 0

export DATABASE_URL="${DATABASE_URL%/*}/${BENCH_DB}"
export QDRANT_COLLECTION="$BENCH_COLLECTION" SEED_DEMO_DATA=false RATE_LIMIT_ENABLED=false
cd /app
alembic upgrade head
echo $$ > "$PIDFILE"
exec uvicorn app.main:app --host 127.0.0.1 --port "${BENCH_PORT:-8001}" --no-server-header --log-level warning

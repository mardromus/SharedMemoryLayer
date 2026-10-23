#!/bin/sh
set -e
# Render hands out postgresql://...; SQLAlchemy needs the psycopg 3 driver name.
case "$DATABASE_URL" in
  postgres://*)   export DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgres://}" ;;
  postgresql://*) export DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgresql://}" ;;
esac

echo "starting embedded qdrant..."
qdrant > /tmp/qdrant.log 2>&1 &
i=0; until curl -sf http://127.0.0.1:6333/readyz >/dev/null; do i=$((i+1)); [ $i -gt 60 ] && { cat /tmp/qdrant.log; exit 1; }; sleep 1; done

echo "applying database migrations..."
cd /app && alembic upgrade head

echo "syncing the admin account with ADMIN_PASSWORD..."
python /app/sync_admin.py

echo "rebuilding the vector index from PostgreSQL..."
python /app/reindex.py

echo "starting API..."
uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-server-header &
i=0; until curl -sf http://127.0.0.1:8000/health/live >/dev/null; do i=$((i+1)); [ $i -gt 180 ] && exit 1; sleep 1; done

echo "starting nginx on port ${PORT}..."
sed "s/__PORT__/${PORT}/" /etc/nginx/sml.conf.template > /etc/nginx/conf.d/sml.conf
exec nginx -g 'daemon off;'

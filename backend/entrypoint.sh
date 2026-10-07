#!/bin/sh
set -e

# Apply database migrations before serving traffic (idempotent).
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Applying database migrations..."
  alembic upgrade head
fi

exec "$@"

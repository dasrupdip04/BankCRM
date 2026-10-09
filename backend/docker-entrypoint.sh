#!/bin/sh
set -eu

echo "Waiting for PostgreSQL using DATABASE_URL host and port..."
attempt=1
while [ "$attempt" -le 30 ]; do
  if python - <<'PY'
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 3})
with engine.connect() as connection:
    connection.execute(text("SELECT 1"))
PY
  then
    break
  fi
  echo "PostgreSQL not reachable yet (attempt $attempt/30); retrying in 2s..."
  attempt=$((attempt + 1))
  sleep 2
done

if [ "$attempt" -gt 30 ]; then
  echo "ERROR: PostgreSQL could not be reached after 30 attempts. Check DATABASE_URL and Compose networking." >&2
  exit 1
fi

echo "PostgreSQL connection verified; applying Alembic migrations..."
if ! alembic upgrade head; then
  echo "ERROR: Alembic migration failed; API will not start." >&2
  exit 1
fi

echo "Migrations complete; starting FastAPI on 0.0.0.0:8000."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

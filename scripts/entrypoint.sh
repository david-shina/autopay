#!/usr/bin/env bash
# ─── Entrypoint: wait for DB, run migrations, then start the app ───
set -euo pipefail

echo "[entrypoint] starting at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── Wait for Postgres (only if DATABASE_URL points at a real DB) ──
if [[ "${DATABASE_URL:-}" == postgresql* ]]; then
  echo "[entrypoint] waiting for database..."
  python - <<'PY'
import os
import sys
import time

import psycopg2
from psycopg2 import OperationalError

url = os.environ["DATABASE_URL"]
deadline = time.time() + 60
while True:
    try:
        conn = psycopg2.connect(url, connect_timeout=3)
        conn.close()
        print("[entrypoint] database reachable")
        break
    except OperationalError as e:
        if time.time() > deadline:
            print(f"[entrypoint] database not reachable after 60s: {e}", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)
PY
fi

# ── Run migrations unless explicitly skipped ──
if [[ "${SKIP_MIGRATIONS:-0}" != "1" ]]; then
  echo "[entrypoint] running alembic upgrade head"
  alembic upgrade head
fi

# ── Hand off to CMD ──
echo "[entrypoint] handing off to: $*"
exec "$@"

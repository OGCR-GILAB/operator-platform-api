"""Block until PostgreSQL accepts connections (or DB_WAIT_SECONDS elapse)."""

import os
import sys
import time

import psycopg

conninfo = {
    "host": os.environ.get("DB_HOST", "db"),
    "port": os.environ.get("DB_PORT", "5432"),
    "dbname": os.environ.get("POSTGRES_DB", "operator"),
    "user": os.environ.get("POSTGRES_USER", "operator"),
    "password": os.environ.get("POSTGRES_PASSWORD", "operator"),
    "connect_timeout": 3,
}
deadline = time.monotonic() + int(os.environ.get("DB_WAIT_SECONDS", "60"))

while True:
    try:
        with psycopg.connect(**conninfo):
            pass
    except psycopg.OperationalError as exc:
        if time.monotonic() > deadline:
            print(f"database not reachable: {exc}", file=sys.stderr)
            sys.exit(1)
        print("waiting for database...")
        time.sleep(2)
    else:
        print("database is ready")
        sys.exit(0)

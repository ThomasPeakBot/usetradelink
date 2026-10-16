"""Database access and versioned migrations."""
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_LOCK = 915202611


def connect(url=None, **kwargs):
    return psycopg.connect(url or os.environ["DATABASE_URL"], connect_timeout=10,
                           row_factory=dict_row, **kwargs)


def migrate(conn):
    """Apply pending migrations in order, once, under an advisory lock."""
    applied = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        conn.execute("CREATE TABLE IF NOT EXISTS engine_schema_migrations ("
                     "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r["version"] for r in conn.execute("SELECT version FROM engine_schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.stem in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO engine_schema_migrations(version) VALUES(%s)", (path.stem,))
            applied.append(path.stem)
    return applied


def table_exists(conn, name):
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", ("public." + name,)).fetchone()["ok"]

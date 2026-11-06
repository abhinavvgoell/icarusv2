"""Small compatibility layer while Arbiter transitions from SQLite to PostgreSQL."""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path


def _postgres_sql(sql: str) -> str:
    """Translate this application's parameter and idempotent-insert dialect."""
    sql = sql.replace("?", "%s")
    if sql.lstrip().upper().startswith("INSERT OR IGNORE"):
        sql = re.sub(r"INSERT OR IGNORE", "INSERT", sql, count=1, flags=re.I).rstrip()
        sql = f"{sql} ON CONFLICT DO NOTHING"
    return sql


class PostgresConnection:
    is_postgres = True

    def __init__(self, connection): self.connection = connection
    def execute(self, sql: str, params=()): return self.connection.execute(_postgres_sql(sql), params)
    def executemany(self, sql: str, params):
        with self.connection.cursor() as cursor:
            return cursor.executemany(_postgres_sql(sql), params)
    def executescript(self, script: str):
        for statement in script.split(";"):
            if statement.strip(): self.execute(statement)
    def commit(self): self.connection.commit()
    def close(self): self.connection.close()


def database_path(default: Path) -> Path:
    return Path(os.getenv("APP_DATABASE_PATH", default))


def connect(default_sqlite_path: Path):
    url = os.getenv("APP_DATABASE_URL", "").strip()
    if not url:
        conn = sqlite3.connect(database_path(default_sqlite_path))
        conn.row_factory = sqlite3.Row
        return conn
    if not url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("APP_DATABASE_URL must be a PostgreSQL connection URL")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Install psycopg to use APP_DATABASE_URL") from exc
    return PostgresConnection(psycopg.connect(url, row_factory=dict_row))


def is_postgres(connection) -> bool:
    return isinstance(connection, PostgresConnection)

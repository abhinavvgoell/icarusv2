"""One-time, guarded migration of an Icarus SQLite database to PostgreSQL.

Set APP_DATABASE_URL, APP_STORAGE_BACKEND, and APP_UPLOAD_DIR in the process
environment. For production, configure the S3/KMS variables as well. This
command never prints database credentials and intentionally discards sessions.
Stop application writes before starting; the SQLite read transaction provides
a consistent snapshot, but does not capture writes made after that snapshot.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import sha256
from app.storage import LocalStorage, StorageError, make_storage

TABLES = (
    "users",
    "applications",
    "sessions",
    "login_attempts",
    "documents",
    "extracted_fields",
    "findings",
    "audit_events",
    "verification_attempts",
    "facility_reviews",
    "rule_settings",
    "clarifications",
)
SKIP_TABLES = {"sessions", "login_attempts"}  # Sessions and throttle windows are ephemeral security state.
OPTIONAL_SOURCE_TABLES = {"facility_reviews"}  # Added after some local demo databases were created.
BOOLEAN_COLUMNS = {("users", "active"), ("rule_settings", "enabled")}


class MigrationError(RuntimeError):
    pass


def _source_db(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise MigrationError(f"SQLite source does not exist: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def _assert_source_ready(source: sqlite3.Connection) -> None:
    tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Older local databases have no throttle table; its counters are ephemeral
    # and intentionally excluded from the migration payload.
    missing = set(TABLES) - SKIP_TABLES - OPTIONAL_SOURCE_TABLES - tables
    if missing:
        raise MigrationError("SQLite source is missing tables: " + ", ".join(sorted(missing)))
    demo = source.execute("SELECT 1 FROM users WHERE lower(email) LIKE '%.demo' LIMIT 1").fetchone()
    if demo:
        raise MigrationError("Refusing to migrate synthetic .demo identities; prepare a reviewed, sanitized source first")


def _verify_document_files(source: sqlite3.Connection, source_uploads: Path) -> list[tuple[str, str, bytes]]:
    files = []
    for row in source.execute("SELECT storage_name, hash FROM documents ORDER BY id"):
        key = Path(row["storage_name"])
        if key.is_absolute() or ".." in key.parts:
            raise MigrationError("A document has an unsafe storage key")
        try:
            content = LocalStorage(source_uploads).get(key.as_posix())
        except StorageError as exc:
            raise MigrationError(f"A source document is missing: {key.name}") from exc
        if sha256(content) != row["hash"]:
            raise MigrationError(f"A source document failed its SHA-256 check: {key.name}")
        files.append((key.as_posix(), row["hash"], content))
    return files


def _target_schema_and_empty(connection) -> dict[str, set[str]]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema=current_schema()")
        columns: dict[str, set[str]] = {}
        for table, column in cursor.fetchall():
            columns.setdefault(table, set()).add(column)
        for table in TABLES:
            if table not in columns:
                raise MigrationError(f"PostgreSQL target is missing table: {table}; apply the reviewed schema first")
            cursor.execute(f'SELECT 1 FROM "{table}" LIMIT 1')
            if cursor.fetchone():
                raise MigrationError(f"PostgreSQL target is not empty (table: {table})")
    return columns


def _copy_objects(storage, files: list[tuple[str, str, bytes]]) -> None:
    storage.initialize()
    for key, expected_hash, content in files:
        if storage.exists(key):
            existing = storage.get(key)
            if sha256(existing) != expected_hash:
                raise MigrationError(f"A different object already exists at destination key: {key}")
            continue
        storage.put(key, content)


def _insert_rows(source: sqlite3.Connection, connection, target_columns: dict[str, set[str]]) -> dict[str, int]:
    migrated = {}
    with connection.cursor() as cursor:
        cursor.execute("LOCK TABLE " + ", ".join(f'"{table}"' for table in TABLES) + " IN EXCLUSIVE MODE")
        for table in TABLES:
            cursor.execute(f'SELECT 1 FROM "{table}" LIMIT 1')
            if cursor.fetchone():
                raise MigrationError(f"PostgreSQL target changed during migration (table: {table})")

        for table in TABLES:
            if table in SKIP_TABLES:
                migrated[table] = 0
                continue
            if table in OPTIONAL_SOURCE_TABLES and table not in {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                migrated[table] = 0
                continue
            source_cursor = source.execute(f'SELECT * FROM "{table}"')
            names = [column[0] for column in source_cursor.description]
            absent = set(names) - target_columns[table]
            if absent:
                raise MigrationError(f"PostgreSQL target lacks columns for {table}: " + ", ".join(sorted(absent)))
            quoted_names = ",".join(f'"{name}"' for name in names)
            placeholders = ",".join(["%s"] * len(names))
            insert_sql = f'INSERT INTO "{table}" ({quoted_names}) VALUES ({placeholders})'
            count = 0
            while batch := source_cursor.fetchmany(500):
                values = []
                for row in batch:
                    converted = []
                    for name, value in zip(names, row):
                        if (table, name) in BOOLEAN_COLUMNS and value is not None:
                            value = bool(value)
                        converted.append(value)
                    values.append(converted)
                cursor.executemany(insert_sql, values)
                count += len(batch)
            migrated[table] = count

        for table in TABLES:
            if "id" in target_columns[table]:
                cursor.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1), COUNT(*) > 0) FROM \"{table}\"")
    return migrated


def migrate(source_path: Path, source_uploads: Path) -> dict[str, int]:
    database_url = os.getenv("APP_DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise MigrationError("Set APP_DATABASE_URL to the empty PostgreSQL target")
    if not os.getenv("APP_UPLOAD_DIR", "").strip():
        raise MigrationError("Set APP_UPLOAD_DIR to the destination storage root")
    try:
        import psycopg
    except ImportError as exc:
        raise MigrationError("Install the project requirements before running migration") from exc

    source = _source_db(source_path)
    storage = make_storage(Path(os.environ["APP_UPLOAD_DIR"]))
    target = None
    try:
        _assert_source_ready(source)
        files = _verify_document_files(source, source_uploads)
        target = psycopg.connect(database_url)
        # End the read transaction before beginning the atomic insert transaction.
        with target.transaction():
            _target_schema_and_empty(target)
        _copy_objects(storage, files)
        with target.transaction():
            columns = _target_schema_and_empty(target)
            migrated = _insert_rows(source, target, columns)
        return migrated
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(f"Migration failed ({type(exc).__name__}); target transaction was rolled back") from exc
    finally:
        source.close()
        if target is not None:
            target.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path, help="SQLite database file to migrate")
    parser.add_argument("--source-uploads", type=Path, help="SQLite upload directory (defaults to <source-db-parent>/uploads)")
    args = parser.parse_args()
    uploads = args.source_uploads or args.source_db.parent / "uploads"
    try:
        counts = migrate(args.source_db, uploads)
    except MigrationError as exc:
        print(f"Migration stopped: {exc}", file=sys.stderr)
        return 1
    print("Migration committed. Imported rows:")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    print("  sessions: 0 (all prior sessions revoked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os
import sqlite3
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .rules import CHECKLIST, DEMO_REVIEW_CYCLES, DEMO_REQUEST_TYPE_LABELS, RULES, RULE_BY_ID, RULE_PACK
from .services import extract_fields, extract_fields_and_quotes, extract_page_texts, extract_fields_with_pages, extract_fields_with_pages_and_quotes, extract_text, run_checks, select_rule_pack, sha256
from .storage import StorageError, make_storage
from .database import connect, is_postgres
from .malware import MalwareScanUnavailable, check as check_malware_scanner, scan
from .observability import configure_logging, request_id
from . import ai

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("APP_DATABASE_PATH", DATA_DIR / "prototype.db"))
APP_ENV = os.getenv("APP_ENV", "development").lower()
BOOTSTRAP_DEMO_DATA = os.getenv("APP_BOOTSTRAP_DEMO_DATA", "false" if APP_ENV == "production" else "true").lower() == "true"
AUTH_MODE = os.getenv("APP_AUTH_MODE", "local").lower()
SESSION_COOKIE_SECRET = os.getenv("APP_SESSION_COOKIE_SECRET", "development-only-change-me")
AUDIT_RETENTION_DAYS = int(os.getenv("APP_AUDIT_RETENTION_DAYS", "2555"))
logger = configure_logging()
UPLOAD_DIR = Path(os.getenv("APP_UPLOAD_DIR", DATA_DIR / "uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("APP_MAX_UPLOAD_BYTES", "5242880"))
SESSION_TTL_HOURS = int(os.getenv("APP_SESSION_TTL_HOURS", "8"))
LOGIN_ATTEMPTS_PER_WINDOW = int(os.getenv("APP_LOGIN_ATTEMPTS_PER_WINDOW", "10"))
LOGIN_WINDOW_SECONDS = int(os.getenv("APP_LOGIN_WINDOW_SECONDS", "900"))
ALLOWED_HOSTS = [host.strip() for host in os.getenv("APP_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",") if host.strip()]
ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("APP_ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",") if origin.strip()]
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".csv", ".png", ".jpg", ".jpeg", ".mp4"}
storage = make_storage(UPLOAD_DIR)

@asynccontextmanager
async def lifespan(_: FastAPI):
    if APP_ENV == "production":
        errors = production_configuration_errors(dict(os.environ))
        if errors: raise RuntimeError("Invalid production configuration: " + "; ".join(errors))
    init_db()
    if APP_ENV == "production":
        conn = db()
        demo_identity = conn.execute("SELECT 1 FROM users WHERE lower(email) LIKE '%.demo' LIMIT 1").fetchone()
        conn.close()
        if demo_identity: raise RuntimeError("Production database contains synthetic .demo identities; migrate to a clean, reviewed production database")
    yield

app = FastAPI(title="Icarus — Document Compliance Review", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_COOKIE_SECRET, https_only=APP_ENV == "production", same_site="lax")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type", "X-Session-Token"])
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
def now() -> str: return datetime.now(timezone.utc).isoformat()
def session_expiry() -> str: return (datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
def session_token_hash(token: str) -> str: return hashlib.sha256(token.encode("utf-8")).hexdigest()
def audit_event_hash(previous_hash: str, application_id: int, actor: str, action: str, detail: str, created_at) -> str:
    timestamp = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)
    return hashlib.sha256(f"{previous_hash}|{application_id}|{actor}|{action}|{detail}|{timestamp}".encode()).hexdigest()
def reserve_login_attempt(conn, account_key: str) -> int:
    """Atomically count an attempt in the shared database across app replicas."""
    key = hashlib.sha256(account_key.encode("utf-8")).hexdigest()
    timestamp = time.time()
    conn.execute("DELETE FROM login_attempts WHERE updated_at < ?", (timestamp - (2 * LOGIN_WINDOW_SECONDS),))
    row = conn.execute(
        """INSERT INTO login_attempts(account_key, attempts, window_started_at, updated_at)
           VALUES(?,1,?,?)
           ON CONFLICT(account_key) DO UPDATE SET
             attempts=CASE WHEN ? - login_attempts.window_started_at >= ? THEN 1 ELSE login_attempts.attempts + 1 END,
             window_started_at=CASE WHEN ? - login_attempts.window_started_at >= ? THEN ? ELSE login_attempts.window_started_at END,
             updated_at=?
           RETURNING attempts""",
        (key, timestamp, timestamp, timestamp, LOGIN_WINDOW_SECONDS, timestamp, LOGIN_WINDOW_SECONDS, timestamp, timestamp),
    ).fetchone()
    conn.commit()
    return int(row["attempts"])

def clear_login_attempts(conn, account_key: str) -> None:
    key = hashlib.sha256(account_key.encode("utf-8")).hexdigest()
    conn.execute("DELETE FROM login_attempts WHERE account_key=?", (key,))
def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310_000).hex()
    return f"{salt}${digest}"
def verify_password(password: str, saved: str | None) -> bool:
    if not saved or "$" not in saved: return False
    salt, digest = saved.split("$", 1)
    return secrets.compare_digest(hash_password(password, salt).split("$", 1)[1], digest)
def db():
    return connect(DB_PATH)

def insert_id(conn, sql: str, params) -> int:
    if is_postgres(conn):
        return conn.execute(f"{sql} RETURNING id", params).fetchone()["id"]
    return conn.execute(sql, params).lastrowid

def user(authorization: str | None = Header(None), x_session_token: str | None = Header(None), icarus_session: str | None = Cookie(None)):
    conn = db(); item = None
    token = x_session_token or icarus_session or (authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None)
    if token:
        token_hash = session_token_hash(token)
        item = conn.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at > ? AND u.active=TRUE", (token_hash, now())).fetchone()
        if not item: conn.execute("DELETE FROM sessions WHERE token=?", (token_hash,)); conn.commit()
    if not item: conn.close(); raise HTTPException(401, "Sign-in is required")
    conn.close()
    return item

def audit(conn, application_id: int, actor: sqlite3.Row, action: str, detail: str):
    if is_postgres(conn):
        # Serialize chain extension for this application so concurrent writers
        # cannot both append an event using the same previous hash.
        conn.execute("SELECT id FROM applications WHERE id=? FOR UPDATE", (application_id,))
    previous = conn.execute("SELECT event_hash FROM audit_events WHERE application_id=? AND event_hash IS NOT NULL ORDER BY id DESC LIMIT 1", (application_id,)).fetchone()
    previous_hash = previous["event_hash"] if previous else ""
    created_at = now()
    event_hash = audit_event_hash(previous_hash, application_id, actor["email"], action, detail, created_at)
    conn.execute("INSERT INTO audit_events(application_id, actor, action, detail, created_at, previous_hash, event_hash) VALUES(?,?,?,?,?,?,?)", (application_id, actor["email"], action, detail, created_at, previous_hash or None, event_hash))

def seed_baseline_rule_pack(conn):
    """Persist the immutable baseline pack once, callable during import-time init.

    rule_pack_versions must exist from the first request: pre-check and submission
    select a published pack, and they must not depend on someone visiting the rule
    registry page first.
    """
    if conn.execute("SELECT 1 FROM rule_pack_versions LIMIT 1").fetchone():
        return
    saved = {row["rule_id"]: bool(row["enabled"]) for row in conn.execute("SELECT rule_id,enabled FROM rule_settings")}
    snapshot = {rule["id"]: bool(rule["executable"] and saved.get(rule["id"], rule["enabled"])) for rule in RULES if rule["executable"]}
    conn.execute(
        "INSERT INTO rule_pack_versions(version,snapshot_json,status,change_reason,created_by,created_at,approved_by,published_at) VALUES(?,?,?,?,?,?,?,?)",
        (RULE_PACK["version"], json.dumps(snapshot, sort_keys=True), "published", "Initial local demo rule pack baseline", "system", now(), "system", now()),
    )

def init_db():
    if not os.getenv("APP_DATABASE_URL", "").strip():
        DATA_DIR.mkdir(exist_ok=True)
    storage.initialize()
    conn = db()
    if is_postgres(conn):
        try:
            conn.execute("SELECT 1 FROM users LIMIT 1")
            conn.execute("SELECT 1 FROM login_attempts LIMIT 1")
            conn.execute("SELECT 1 FROM document_ai_analysis LIMIT 1")
            conn.execute("SELECT source FROM extracted_fields LIMIT 1")
            conn.execute("SELECT quote FROM extracted_fields LIMIT 1")
            conn.execute("SELECT assigned_to, escalation_level, escalation_reason, escalated_at, escalated_by FROM applications LIMIT 1")
            conn.execute("SELECT source FROM verification_attempts LIMIT 1")
            conn.execute("SELECT outcome FROM facility_reviews LIMIT 1")
            conn.execute("SELECT snapshot_json FROM rule_pack_versions LIMIT 1")
        except Exception as exc:
            conn.close()
            raise RuntimeError("PostgreSQL schema is missing or outdated. Apply the reviewed SQL migrations in infra/postgres through the migration pipeline.") from exc
        defaults = ["DOCS-REQUIRED-001", "NAME-MATCH-001", "FIELD-CONFLICT-001", "CAPACITY-MATCH-001", "SCOPE-MATCH-001", "EXPIRY-001", "DUPLICATE-001", "EXTRACT-001", "EXTRACT-002"]
        conn.executemany("INSERT OR IGNORE INTO rule_settings(rule_id, enabled, updated_at, updated_by) VALUES(?,TRUE,?,?)", [(rule, now(), "system") for rule in defaults])
        seed_baseline_rule_pack(conn)
        if BOOTSTRAP_DEMO_DATA:
            has_users = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
            has_applications = conn.execute("SELECT 1 FROM applications LIMIT 1").fetchone()
            if not has_users and not has_applications:
                conn.executemany("INSERT INTO users(email, role, organization_id, password_hash) VALUES(?,?,?,?)", [
                    ("submitter@northwind.demo", "submitter", "ORG-1001", hash_password("Submitter@123")), ("submitter@cascade.demo", "submitter", "ORG-1002", hash_password("Submitter@123")), ("reviewer@icarus.demo", "reviewer", None, hash_password("Reviewer@123")), ("admin@icarus.demo", "admin", None, hash_password("Admin@123"))])
                conn.executemany("INSERT INTO applications(reference, organization_id, organization_name, request_type, review_cycle, status, created_at, scope_name, requested_capacity) VALUES(?,?,?,?,?,?,?,?,?)", [
                    ("SUB-2026-1001", "ORG-1001", "Northwind Logistics Ltd", "licence_renewal", "2026-27", "draft", now(), "Bulk Storage", 120),
                    ("SUB-2026-1002", "ORG-1002", "Cascade Chemicals Ltd", "licence_renewal", "2026-27", "draft", now(), "Chemical Handling", 200)])
                conn.commit()
                seed_documents(conn)
                ensure_extended_demo(conn)
            elif not has_users or not has_applications:
                conn.close()
                raise RuntimeError("Refusing demo bootstrap into a partially populated PostgreSQL database")
        conn.commit()
        conn.close()
        return
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, role TEXT, organization_id TEXT, password_hash TEXT, active INTEGER NOT NULL DEFAULT 1, oidc_subject TEXT UNIQUE);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT, expires_at TEXT);
    CREATE TABLE IF NOT EXISTS login_attempts(account_key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, window_started_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY, reference TEXT UNIQUE, organization_id TEXT, organization_name TEXT, request_type TEXT, review_cycle TEXT, status TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, application_id INTEGER, document_type TEXT, original_name TEXT, storage_name TEXT, hash TEXT, confidence REAL, extraction_error TEXT, created_at TEXT, supersedes_document_id INTEGER);
    CREATE TABLE IF NOT EXISTS extracted_fields(id INTEGER PRIMARY KEY, document_id INTEGER, field_name TEXT, raw_value TEXT, normalized_value TEXT, page INTEGER, confidence REAL, source TEXT NOT NULL DEFAULT 'pattern', quote TEXT);
    CREATE TABLE IF NOT EXISTS document_ai_analysis(document_id INTEGER PRIMARY KEY REFERENCES documents(id), status TEXT NOT NULL, suggested_type TEXT, similarity REAL, model TEXT, detail TEXT NOT NULL, analyzed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS findings(id INTEGER PRIMARY KEY, application_id INTEGER, document_id INTEGER, kind TEXT, severity TEXT, title TEXT, explanation TEXT, evidence TEXT, rule_id TEXT, rule_version TEXT, status TEXT, decision_reason TEXT, reviewed_by TEXT, evidence_page INTEGER);
    CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY, application_id INTEGER, actor TEXT, action TEXT, detail TEXT, created_at TEXT, previous_hash TEXT, event_hash TEXT);
    CREATE TABLE IF NOT EXISTS verification_attempts(id INTEGER PRIMARY KEY, application_id INTEGER, document_id INTEGER, method TEXT, status TEXT, mode TEXT, detail TEXT, checked_at TEXT);
    CREATE TABLE IF NOT EXISTS facility_reviews(id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, document_id INTEGER NOT NULL, reviewer TEXT NOT NULL, outcome TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS rule_settings(rule_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT, updated_by TEXT);
    CREATE TABLE IF NOT EXISTS rule_pack_versions(id INTEGER PRIMARY KEY, version TEXT NOT NULL, snapshot_json TEXT NOT NULL, status TEXT NOT NULL, change_reason TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, approved_by TEXT, published_at TEXT);
    CREATE TABLE IF NOT EXISTS clarifications(id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, message TEXT NOT NULL, requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, response TEXT, responded_by TEXT, responded_at TEXT, status TEXT NOT NULL DEFAULT 'open');
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS login_attempts_updated_at_idx ON login_attempts(updated_at)")
    for statement in ("ALTER TABLE users ADD COLUMN password_hash TEXT", "ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1", "ALTER TABLE users ADD COLUMN oidc_subject TEXT", "ALTER TABLE sessions ADD COLUMN expires_at TEXT", "ALTER TABLE applications ADD COLUMN address TEXT", "ALTER TABLE applications ADD COLUMN scope_name TEXT", "ALTER TABLE applications ADD COLUMN requested_capacity INTEGER", "ALTER TABLE applications ADD COLUMN updated_at TEXT", "ALTER TABLE documents ADD COLUMN malware_scan_status TEXT", "ALTER TABLE documents ADD COLUMN malware_scan_detail TEXT", "ALTER TABLE documents ADD COLUMN scanned_at TEXT", "ALTER TABLE documents ADD COLUMN supersedes_document_id INTEGER", "ALTER TABLE audit_events ADD COLUMN previous_hash TEXT", "ALTER TABLE audit_events ADD COLUMN event_hash TEXT", "ALTER TABLE extracted_fields ADD COLUMN source TEXT NOT NULL DEFAULT 'pattern'", "ALTER TABLE extracted_fields ADD COLUMN quote TEXT", "ALTER TABLE findings ADD COLUMN evidence_page INTEGER", "ALTER TABLE applications ADD COLUMN assigned_to TEXT", "ALTER TABLE applications ADD COLUMN escalation_level TEXT", "ALTER TABLE applications ADD COLUMN escalation_reason TEXT", "ALTER TABLE applications ADD COLUMN escalated_at TEXT", "ALTER TABLE applications ADD COLUMN escalated_by TEXT", "ALTER TABLE verification_attempts ADD COLUMN source TEXT"):
        try: conn.execute(statement)
        except sqlite3.OperationalError: pass
    if BOOTSTRAP_DEMO_DATA and not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        conn.executemany("INSERT INTO users(email, role, organization_id, password_hash) VALUES(?,?,?,?)", [
            ("submitter@northwind.demo", "submitter", "ORG-1001", hash_password("Submitter@123")), ("submitter@cascade.demo", "submitter", "ORG-1002", hash_password("Submitter@123")), ("reviewer@icarus.demo", "reviewer", None, hash_password("Reviewer@123"))])
        conn.executemany("INSERT INTO applications(reference, organization_id, organization_name, request_type, review_cycle, status, created_at, scope_name, requested_capacity) VALUES(?,?,?,?,?,?,?,?,?)", [
            ("SUB-2026-1001", "ORG-1001", "Northwind Logistics Ltd", "licence_renewal", "2026-27", "draft", now(), "Bulk Storage", 120),
            ("SUB-2026-1002", "ORG-1002", "Cascade Chemicals Ltd", "licence_renewal", "2026-27", "draft", now(), "Chemical Handling", 200)])
        conn.commit()
        seed_documents(conn)
    if BOOTSTRAP_DEMO_DATA:
        conn.execute("INSERT OR IGNORE INTO users(email, role, organization_id, password_hash) VALUES(?,?,?,?)", ("admin@icarus.demo", "admin", None, hash_password("Admin@123")))
    defaults = ["DOCS-REQUIRED-001", "NAME-MATCH-001", "FIELD-CONFLICT-001", "CAPACITY-MATCH-001", "SCOPE-MATCH-001", "EXPIRY-001", "DUPLICATE-001", "EXTRACT-001", "EXTRACT-002"]
    conn.executemany("INSERT OR IGNORE INTO rule_settings(rule_id, enabled, updated_at, updated_by) VALUES(?,1,?,?)", [(rule, now(), "system") for rule in defaults])
    seed_baseline_rule_pack(conn)
    conn.execute("UPDATE sessions SET expires_at=? WHERE expires_at IS NULL OR expires_at=''", (session_expiry(),))
    conn.execute("UPDATE users SET active=1 WHERE active IS NULL")
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now(),))
    # Existing local demo databases predate password hashes.
    for email, password in (("submitter@northwind.demo", "Submitter@123"), ("submitter@cascade.demo", "Submitter@123"), ("reviewer@icarus.demo", "Reviewer@123")):
        conn.execute("UPDATE users SET password_hash=? WHERE email=? AND (password_hash IS NULL OR password_hash='')", (hash_password(password), email))
    conn.commit()
    if BOOTSTRAP_DEMO_DATA:
        ensure_extended_demo(conn)
    conn.close()

def production_configuration_errors(env: dict[str, str]) -> list[str]:
    """Return missing or unsafe settings that must block production startup."""
    errors = []
    if RULE_PACK.get("provisional", True):
        errors.append("The configured regulatory rule pack is provisional and cannot be used for production assessment")
    if env.get("APP_BOOTSTRAP_DEMO_DATA", "false").lower() == "true":
        errors.append("APP_BOOTSTRAP_DEMO_DATA must be false")
    if not env.get("APP_DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
        errors.append("APP_DATABASE_URL must point to PostgreSQL")
    if env.get("APP_STORAGE_BACKEND", "local").lower() != "s3":
        errors.append("APP_STORAGE_BACKEND must be s3")
    if not env.get("APP_S3_BUCKET", "").strip():
        errors.append("APP_S3_BUCKET is required")
    if not env.get("APP_S3_REGION", "").strip():
        errors.append("APP_S3_REGION is required")
    if env.get("APP_S3_ENCRYPTION", "aws:kms").lower() != "aws:kms" or not env.get("APP_S3_KMS_KEY_ID", "").strip():
        errors.append("APP_S3_ENCRYPTION=aws:kms and APP_S3_KMS_KEY_ID are required")
    if env.get("APP_AUTH_MODE", "local").lower() != "oidc":
        errors.append("APP_AUTH_MODE must be oidc")
    if not env.get("APP_OIDC_ISSUER", "").startswith("https://"):
        errors.append("APP_OIDC_ISSUER must be an HTTPS issuer URL")
    for name in ("APP_OIDC_CLIENT_ID", "APP_OIDC_CLIENT_SECRET"):
        if not env.get(name, "").strip(): errors.append(f"{name} is required")
    if not {domain.strip().lower() for domain in env.get("APP_OIDC_ALLOWED_DOMAINS", "").split(",") if domain.strip()}:
        errors.append("APP_OIDC_ALLOWED_DOMAINS must contain an approved domain")
    secret = env.get("APP_SESSION_COOKIE_SECRET", "")
    if len(secret) < 32 or secret == "development-only-change-me":
        errors.append("APP_SESSION_COOKIE_SECRET must be a non-default secret of at least 32 characters")
    if env.get("APP_MALWARE_SCAN_MODE", "disabled").lower() != "clamav":
        errors.append("APP_MALWARE_SCAN_MODE must be clamav")
    if not env.get("APP_CLAMAV_HOST", "").strip():
        errors.append("APP_CLAMAV_HOST is required")
    hosts = [host.strip().lower() for host in env.get("APP_ALLOWED_HOSTS", "").split(",") if host.strip()]
    if not hosts or "*" in hosts or any(host in {"localhost", "127.0.0.1", "testserver"} for host in hosts):
        errors.append("APP_ALLOWED_HOSTS must explicitly list production hostnames")
    origins = [origin.strip() for origin in env.get("APP_ALLOWED_ORIGINS", "").split(",") if origin.strip()]
    if not origins or any(not origin.startswith("https://") or "*" in origin for origin in origins):
        errors.append("APP_ALLOWED_ORIGINS must explicitly list HTTPS origins")
    return errors

def seed_documents(conn):
    entries = [
      (1, "declaration", "northwind_declaration.txt", "Organization Name: Northwind Logistics Ltd\nCertificate No: DECL-1001\nScope: Bulk Storage\nCapacity: 120\n"),
      (1, "safety_certificate", "northwind_safety.txt", "Organization Name: Northwind Logistics Ltd\nCertificate No: SAFE-1001\nValid Until: 2027-03-31\nScope: Bulk Storage\nCapacity: 120\n"),
      (1, "prior_approval", "northwind_prior.txt", "Organization Name: Northwind Logistics Ltd\nReference No: SUB-2025-1001\nScope: Bulk Storage\n"),
      (2, "declaration", "cascade_declaration.txt", "Organization Name: Cascade Chemicals Ltd\nCertificate No: DECL-2002\nScope: Chemical Handling\nCapacity: 200\n"),
      (2, "safety_certificate", "cascade_safety.txt", "Organization Name: Cascade Chemical Works\nCertificate No: SAFE-2002\nValid Until: 2025-03-31\nScope: Chemical Handling\nCapacity: 200\n"),
    ]
    for app_id, doc_type, filename, text in entries:
        content = text.encode(); digest = sha256(content); storage_name = f"seed-{app_id}-{filename}"; storage.put(storage_name, content)
        document_id = insert_id(conn, "INSERT INTO documents(application_id,document_type,original_name,storage_name,hash,confidence,created_at) VALUES(?,?,?,?,?,?,?)", (app_id, doc_type, filename, storage_name, digest, .98, now()))
        for field, (value, quote) in extract_fields_and_quotes(text).items(): conn.execute("INSERT INTO extracted_fields(document_id,field_name,raw_value,normalized_value,page,confidence,quote) VALUES(?,?,?,?,1,.98,?)", (document_id, field, value, value, quote))
    conn.commit(); run_checks(conn, 1); run_checks(conn, 2)

def add_seed_document(conn, app_id: int, document_type: str, filename: str, content: bytes, confidence: float = .98, error: str | None = None):
    digest = sha256(content); storage_name = f"seed-{app_id}-{filename}"; storage.put(storage_name, content)
    document_id = insert_id(conn, "INSERT INTO documents(application_id,document_type,original_name,storage_name,hash,confidence,extraction_error,created_at) VALUES(?,?,?,?,?,?,?,?)", (app_id, document_type, filename, storage_name, digest, confidence, error, now()))
    if not error:
        text = content.decode("utf-8", errors="replace")
        for field, (value, quote) in extract_fields_and_quotes(text).items():
            conn.execute("INSERT INTO extracted_fields(document_id,field_name,raw_value,normalized_value,page,confidence,quote) VALUES(?,?,?,?,1,?,?)", (document_id, field, value, value, confidence, quote))
    return document_id

def ensure_extended_demo(conn):
    """Adds a third synthetic scenario without changing a user's uploaded cases."""
    meridian = conn.execute("SELECT id FROM applications WHERE reference='SUB-2026-1003'").fetchone()
    if not meridian:
        app_id = insert_id(conn, "INSERT INTO applications(reference,organization_id,organization_name,request_type,review_cycle,status,created_at,scope_name,requested_capacity) VALUES(?,?,?,?,?,?,?,?,?)", ("SUB-2026-1003", "ORG-1003", "Meridian Waste Services", "licence_renewal", "2026-27", "draft", now(), "Waste Collection", 60))
        declaration = b"Organization Name: Meridian Waste Services\nCertificate No: DECL-3003\n"
        first_id = add_seed_document(conn, app_id, "declaration", "meridian_declaration.txt", declaration)
        add_seed_document(conn, app_id, "facility_declaration", "meridian_declaration_copy.txt", declaration)
        unreadable_id = add_seed_document(conn, app_id, "prior_approval", "meridian_scan.pdf", b"not-a-real-pdf", 0.0, "Unreadable PDF; manual review required")
        conn.executemany("INSERT INTO verification_attempts(application_id,document_id,method,status,mode,detail,checked_at) VALUES(?,?,?,?,?,?,?)", [
            (app_id, first_id, "External reference lookup", "verification_pending", "simulated", "Simulated — not externally verified. No external source was contacted.", now()),
            (app_id, unreadable_id, "Manual document review", "manual_review_required", "manual", "Text extraction unavailable; a person must review this document.", now()),
        ])
        conn.commit(); run_checks(conn, app_id)
    if not conn.execute("SELECT 1 FROM verification_attempts LIMIT 1").fetchone():
        docs = {row["application_id"]: row["id"] for row in conn.execute("SELECT application_id, min(id) as id FROM documents GROUP BY application_id")}
        conn.executemany("INSERT INTO verification_attempts(application_id,document_id,method,status,mode,detail,checked_at) VALUES(?,?,?,?,?,?,?)", [
            (1, docs[1], "Issuer route assessment", "source_unavailable", "manual", "No authorized external verification route configured for this prototype.", now()),
            (2, docs[2], "Document review queue", "manual_review_required", "manual", "Conflicting extracted values need reviewer review.", now()),
        ])
        conn.commit()

# Keep the seeded demo available to command-line tests and local development,
# but do not touch production resources before startup configuration is checked.
if APP_ENV != "production":
    init_db()

@app.middleware("http")
async def security_headers(request, call_next):
    supplied_id = request.headers.get("X-Request-ID", "")
    correlation_id = supplied_id if supplied_id.replace("-", "").isalnum() and len(supplied_id) <= 64 else secrets.token_hex(16)
    token = request_id.set(correlation_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        logger.info("request_completed", extra={"method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2)})
        response.headers["X-Request-ID"] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data:; connect-src 'self';"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.scheme == "https": response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
    finally:
        request_id.reset(token)

def application_or_404(conn, application_id: int, actor: sqlite3.Row):
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    if not row: raise HTTPException(404, "Application not found")
    if actor["role"] == "submitter" and row["organization_id"] != actor["organization_id"]: raise HTTPException(403, "You may access only your organization's records")
    if actor["role"] == "reviewer" and row["assigned_to"] and row["assigned_to"].lower() != actor["email"].lower():
        # Hide case existence from reviewers outside the assignment; administrators
        # retain global access for workload routing and oversight.
        raise HTTPException(404, "Application not found")
    return row

def checks_are_current(conn, application_id: int) -> bool:
    last = conn.execute("SELECT id,detail FROM audit_events WHERE application_id=? AND action IN ('precheck_run','submitted') ORDER BY id DESC LIMIT 1", (application_id,)).fetchone()
    last_change = conn.execute("SELECT max(id) AS id FROM audit_events WHERE application_id=? AND action IN ('application_created','application_updated','document_uploaded','document_replaced','field_corrected')", (application_id,)).fetchone()["id"]
    if not last or (last_change is not None and last["id"] <= last_change): return False
    case = conn.execute("SELECT review_cycle,request_type FROM applications WHERE id=?", (application_id,)).fetchone()
    from .services import select_rule_pack
    selected, _ = select_rule_pack(conn, case)
    return bool(selected and f"v{selected['version']}" in (last["detail"] or ""))

def serialize_application(conn, row):
    result = dict(row)
    result["request_type_label"] = DEMO_REQUEST_TYPE_LABELS.get(result.get("request_type"), "Unmapped request_type")
    result["checklist"] = CHECKLIST.get(result.get("request_type"), [])
    result["checks_run"] = checks_are_current(conn, row["id"])
    from .services import select_rule_pack
    selected_pack, selected_scope = select_rule_pack(conn, row)
    result["rule_pack"] = {**RULE_PACK, "review_cycle": result.get("review_cycle"), "request_type": result.get("request_type"),
                            "version": selected_pack["version"] if selected_pack else None,
                            "scope": {key: selected_scope.get(key) for key in ("review_cycles", "request_types", "effective_from", "effective_to")}}
    result["rule_execution"] = {rule_id: bool(enabled and row["review_cycle"] in selected_scope.get("review_cycles", DEMO_REVIEW_CYCLES)
                                                    and row["request_type"] in selected_scope.get("request_types", tuple(CHECKLIST)))
                                for rule_id, enabled in selected_scope.get("rules", {}).items()} if selected_pack else {}
    result["documents"] = [dict(x) for x in conn.execute("SELECT d.*, EXISTS(SELECT 1 FROM documents newer WHERE newer.supersedes_document_id=d.id) AS superseded FROM documents d WHERE d.application_id = ? ORDER BY d.id DESC", (row["id"],))]
    for document in result["documents"]:
        document["fields"] = [dict(x) for x in conn.execute("SELECT field_name, raw_value, normalized_value, page, confidence, source, quote FROM extracted_fields WHERE document_id = ?", (document["id"],))]
        analysis = conn.execute("SELECT status, suggested_type, similarity, model, detail, analyzed_at FROM document_ai_analysis WHERE document_id = ?", (document["id"],)).fetchone()
        document["ai_analysis"] = dict(analysis) if analysis else None
    result["findings"] = [dict(x) for x in conn.execute("SELECT * FROM findings WHERE application_id = ? ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END", (row["id"],))]
    for finding in result["findings"]:
        finding["rule_pack_version"] = finding.get("rule_version") or result["rule_pack"].get("version") or RULE_PACK["version"]
        rule = RULE_BY_ID.get(finding.get("rule_id"))
        if rule:
            for key in ("source", "source_version", "clause", "verification_status", "authoritative", "source_quality", "review_cycles", "request_types", "approval_status", "effective_from", "effective_to"):
                if key not in rule: continue
                finding[key] = rule[key]
            finding["rule_status"] = rule["status"]
    result["audit"] = [dict(x) for x in conn.execute("SELECT * FROM audit_events WHERE application_id = ? ORDER BY id DESC", (row["id"],))]
    result["verifications"] = [dict(x) for x in conn.execute("SELECT * FROM verification_attempts WHERE application_id = ? ORDER BY id DESC", (row["id"],))]
    for verification in result["verifications"]:
        verification["label"] = VERIFICATION_STATUS_LABELS.get(verification.get("status"), verification.get("status"))
        verification["requires_manual_confirmation"] = verification_requires_manual(verification.get("status"))
        if verification.get("mode") == "simulated":
            verification["label"] = "Simulated — not externally verified."
            if "Simulated — not externally verified." not in (verification.get("detail") or ""):
                verification["detail"] = "Simulated — not externally verified. " + (verification.get("detail") or "")
        elif verification.get("mode") == "manual":
            verification["label"] = f"Reviewer-recorded — {verification['label']}; source not machine-verified."
    result["clarifications"] = [dict(x) for x in conn.execute("SELECT * FROM clarifications WHERE application_id = ? ORDER BY id DESC", (row["id"],))]
    return result

@app.get("/")
def home(): return FileResponse(ROOT / "static" / "index.html")

@app.get("/api/health")
def health(): return {"status": "ok", "rule_pack": RULE_PACK}

@app.get("/api/ai/status")
def ai_status(actor=Depends(user)):
    return ai.status()

@app.get("/api/health/live")
def live(): return {"status": "live"}

@app.get("/api/health/ready")
def ready():
    try:
        conn = db()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        storage.initialize()
        check_malware_scanner()
    except Exception:
        raise HTTPException(503, "A required service is unavailable")
    return {"status": "ready"}

class LoginInput(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str

class PasswordChange(BaseModel):
    current_password: str = Field(min_length=8, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)

@app.post("/api/auth/login")
def login(body: LoginInput):
    if AUTH_MODE != "local": raise HTTPException(403, "Local-password authentication is disabled")
    account_key = body.email.lower().strip()
    conn = db()
    if reserve_login_attempt(conn, account_key) > LOGIN_ATTEMPTS_PER_WINDOW:
        conn.close(); raise HTTPException(429, "Too many sign-in attempts. Please wait before trying again.")
    account = conn.execute("SELECT * FROM users WHERE email=?", (account_key,)).fetchone()
    if not account or not account["active"] or not verify_password(body.password, account["password_hash"]):
        conn.close(); raise HTTPException(401, "Invalid email or password")
    token = secrets.token_urlsafe(32)
    clear_login_attempts(conn, account_key)
    conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)", (session_token_hash(token), account["id"], now(), session_expiry()))
    conn.commit(); conn.close()
    return {"token": token, "user": {"id": account["id"], "email": account["email"], "role": account["role"], "organization_id": account["organization_id"]}}

@app.get("/api/auth/mode")
def auth_mode(): return {"mode": AUTH_MODE, "oidc_enabled": AUTH_MODE == "oidc"}

def oidc_client():
    if AUTH_MODE != "oidc": raise HTTPException(404, "OpenID Connect is not enabled")
    issuer, client_id, client_secret = os.getenv("APP_OIDC_ISSUER", ""), os.getenv("APP_OIDC_CLIENT_ID", ""), os.getenv("APP_OIDC_CLIENT_SECRET", "")
    if not issuer or not client_id or not client_secret: raise HTTPException(503, "OpenID Connect is not configured")
    try:
        from authlib.integrations.starlette_client import OAuth
    except ImportError as exc:
        raise HTTPException(503, "OpenID Connect dependency is not installed") from exc
    oauth = OAuth()
    oauth.register("icarus", server_metadata_url=f"{issuer.rstrip('/')}/.well-known/openid-configuration", client_id=client_id, client_secret=client_secret, client_kwargs={"scope": "openid email profile"})
    return oauth.icarus

@app.get("/api/auth/oidc/login")
async def oidc_login(request: Request):
    return await oidc_client().authorize_redirect(request, request.url_for("oidc_callback"))

@app.get("/api/auth/oidc/callback", name="oidc_callback")
async def oidc_callback(request: Request):
    token_data = await oidc_client().authorize_access_token(request)
    claims = token_data.get("userinfo") or {}
    email, subject = str(claims.get("email", "")).lower().strip(), str(claims.get("sub", ""))
    allowed_domains = {x.strip().lower() for x in os.getenv("APP_OIDC_ALLOWED_DOMAINS", "").split(",") if x.strip()}
    if not email or not subject or claims.get("email_verified") is not True or (allowed_domains and email.rsplit("@", 1)[-1] not in allowed_domains):
        raise HTTPException(403, "Your identity-provider account is not eligible for Icarus")
    conn = db(); account = conn.execute("SELECT * FROM users WHERE email=? AND active=TRUE", (email,)).fetchone()
    if not account or not account["oidc_subject"] or account["oidc_subject"] != subject:
        conn.close(); raise HTTPException(403, "Your account is not provisioned for Icarus")
    signed_token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)", (session_token_hash(signed_token), account["id"], now(), session_expiry()))
    conn.commit(); conn.close()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("icarus_session", signed_token, max_age=SESSION_TTL_HOURS * 3600, httponly=True, secure=APP_ENV == "production", samesite="lax")
    return response

@app.post("/api/auth/logout")
def logout(response: Response, actor=Depends(user), x_session_token: str | None = Header(None), authorization: str | None = Header(None), icarus_session: str | None = Cookie(None)):
    token = x_session_token or icarus_session or (authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None)
    if token:
        conn = db(); conn.execute("DELETE FROM sessions WHERE token=?", (session_token_hash(token),)); conn.commit(); conn.close()
    response.delete_cookie("icarus_session")
    return {"ok": True}

@app.post("/api/auth/password")
def change_password(body: PasswordChange, actor=Depends(user)):
    if AUTH_MODE != "local": raise HTTPException(403, "Password changes are managed by your identity provider")
    if not verify_password(body.current_password, actor["password_hash"]):
        raise HTTPException(400, "Current password is incorrect")
    conn = db(); conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.new_password), actor["id"])); conn.execute("DELETE FROM sessions WHERE user_id=?", (actor["id"],)); conn.commit(); conn.close()
    return {"ok": True}

@app.get("/api/session")
def session(actor=Depends(user)):
    return {"user": {"id": actor["id"], "email": actor["email"], "role": actor["role"], "organization_id": actor["organization_id"], "malware_scan_mode": os.getenv("APP_MALWARE_SCAN_MODE", "disabled").lower()}, "rule_pack": RULE_PACK, "checklist": CHECKLIST}

class UserProvision(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    role: str = Field(pattern="^(submitter|reviewer|admin)$")
    organization_id: str | None = Field(default=None, max_length=100)
    initial_password: str | None = Field(default=None, min_length=12, max_length=200)
    oidc_subject: str | None = Field(default=None, min_length=1, max_length=255)

class UserLifecycleUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(submitter|reviewer|admin)$")
    organization_id: str | None = Field(default=None, max_length=100)
    active: bool | None = None

@app.get("/api/admin/users")
def list_users(actor=Depends(user)):
    if actor["role"] != "admin": raise HTTPException(403, "Only administrators can view identities")
    conn = db(); users = [dict(row) for row in conn.execute("SELECT id,email,role,organization_id,active,oidc_subject FROM users ORDER BY email")]; conn.close(); return users

@app.post("/api/admin/users")
def provision_user(body: UserProvision, actor=Depends(user)):
    if actor["role"] != "admin": raise HTTPException(403, "Only administrators can provision identities")
    email = body.email.lower().strip()
    if "@" not in email: raise HTTPException(400, "A valid email address is required")
    if body.role == "submitter" and not body.organization_id: raise HTTPException(400, "Submitter identities require an organization ID")
    if AUTH_MODE == "local" and not body.initial_password: raise HTTPException(400, "Local accounts require an initial password of at least 12 characters")
    if AUTH_MODE == "oidc" and body.initial_password: raise HTTPException(400, "Passwords are managed by the identity provider")
    if AUTH_MODE == "oidc" and not body.oidc_subject: raise HTTPException(400, "OIDC accounts must be explicitly bound to a verified identity-provider subject")
    if AUTH_MODE != "oidc" and body.oidc_subject: raise HTTPException(400, "OIDC subjects may be assigned only in OIDC mode")
    conn = db()
    try:
        password_hash = hash_password(body.initial_password) if body.initial_password else None
        user_id = insert_id(conn, "INSERT INTO users(email,role,organization_id,password_hash,active,oidc_subject) VALUES(?,?,?,?,1,?)", (email, body.role, body.organization_id, password_hash, body.oidc_subject))
    except Exception:
        conn.close(); raise HTTPException(409, "An identity with this email already exists")
    conn.commit(); conn.close(); logger.info("identity_provisioned", extra={"status": 201}); return {"id": user_id, "email": email}

@app.patch("/api/admin/users/{user_id}")
def update_user_lifecycle(user_id: int, body: UserLifecycleUpdate, actor=Depends(user)):
    if actor["role"] != "admin": raise HTTPException(403, "Only administrators can manage identities")
    if user_id == actor["id"] and body.active is False: raise HTTPException(400, "You cannot deactivate your own administrator account")
    conn = db(); target = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not target: conn.close(); raise HTTPException(404, "Identity not found")
    role = body.role if body.role is not None else target["role"]
    organization_id = body.organization_id if body.organization_id is not None else target["organization_id"]
    active = bool(body.active) if body.active is not None else target["active"]
    if role == "submitter" and not organization_id: conn.close(); raise HTTPException(400, "Submitter identities require an organization ID")
    conn.execute("UPDATE users SET role=?,organization_id=?,active=? WHERE id=?", (role, organization_id, active, user_id))
    if not active: conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    conn.commit(); conn.close(); logger.info("identity_lifecycle_updated", extra={"status": 200}); return {"ok": True}

@app.get("/api/request-types")
def request_types(actor=Depends(user)):
    return {"scope": "Sample configuration shipped with the demo; not a complete real-world taxonomy.",
            "review_cycles": list(DEMO_REVIEW_CYCLES),
            "request_types": [{"id": key, "label": DEMO_REQUEST_TYPE_LABELS[key], "documents": CHECKLIST[key]} for key in CHECKLIST]}

@app.get("/api/dashboard")
def dashboard(actor=Depends(user)):
    conn = db()
    if actor["role"] == "admin":
        where, params = "", ()
    elif actor["role"] == "reviewer":
        where, params = " WHERE assigned_to IS NULL OR lower(assigned_to)=lower(?)", (actor["email"],)
    else:
        where, params = " WHERE organization_id=?", (actor["organization_id"],)
    applications = [dict(row) for row in conn.execute("SELECT * FROM applications" + where + " ORDER BY id", params)]
    ids = [item["id"] for item in applications]
    marks = ",".join("?" for _ in ids) or "NULL"
    findings = conn.execute(f"SELECT severity, status, count(*) count FROM findings WHERE application_id IN ({marks}) GROUP BY severity, status", ids).fetchall()
    case_counts = conn.execute(f"SELECT count(DISTINCT a.id) AS deficient_count FROM applications a JOIN findings f ON f.application_id=a.id AND f.status='open' WHERE a.id IN ({marks})", ids).fetchone()["deficient_count"]
    result = {"applications": len(applications),
              "open_findings": sum(x["count"] for x in findings if x["status"] == "open"),
              "deficient_applications": case_counts,
              "high_risk_findings": sum(x["count"] for x in findings if x["status"] == "open" and x["severity"] in ("high", "critical")),
              "ready_for_review": sum(x["status"] == "ready_for_review" for x in applications),
              "in_review_workflow": sum(x["status"] in ("submitted", "awaiting_rectification", "rectification_received") for x in applications),
              "reviewed_applications": sum(x["status"] == "reviewed" for x in applications),
              "escalated_applications": sum(bool(x.get("escalated_at")) for x in applications),
              "assigned_applications": sum(bool(x.get("assigned_to")) for x in applications),
              "recent": applications, "finding_breakdown": [dict(x) for x in findings]}
    def breakdown(field):
        counts = {}
        for application in applications:
            value = application.get(field) or "unspecified"
            counts[value] = counts.get(value, 0) + 1
        return dict(sorted(counts.items()))
    result["application_status_breakdown"] = breakdown("status")
    result["request_type_breakdown"] = breakdown("request_type")
    result["review_cycle_breakdown"] = breakdown("review_cycle")
    result["not_checked_applications"] = sum(not checks_are_current(conn, item["id"]) for item in applications)
    conn.close()
    alert_items = alerts(actor)["items"]
    result["open_alerts"] = len(alert_items)
    result["alert_type_breakdown"] = {
        alert_type: sum(item["type"] == alert_type for item in alert_items)
        for alert_type in sorted({item["type"] for item in alert_items})
    }
    return result

@app.get("/api/alerts")
def alerts(actor=Depends(user)):
    conn = db()
    params = ()
    scope = ""
    if actor["role"] == "submitter":
        scope = " AND a.organization_id=?"
        params = (actor["organization_id"],)
    elif actor["role"] == "reviewer":
        scope = " AND (a.assigned_to IS NULL OR lower(a.assigned_to)=lower(?))"
        params = (actor["email"],)
    finding_rows = conn.execute("SELECT f.id AS finding_id, f.application_id, a.reference, a.organization_name, f.title, f.explanation, f.severity, f.status, f.rule_id, f.document_id FROM findings f JOIN applications a ON a.id=f.application_id WHERE f.status='open'" + scope + " ORDER BY CASE f.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, f.id DESC", params).fetchall()
    clarification_rows = conn.execute("SELECT c.id AS clarification_id, c.application_id, a.reference, a.organization_name, c.message, c.requested_at, c.status FROM clarifications c JOIN applications a ON a.id=c.application_id WHERE c.status='open'" + scope + " ORDER BY c.id DESC", params).fetchall()
    pending_verifications = conn.execute("SELECT v.id AS verification_id, v.application_id, a.reference, a.organization_name, v.method, v.status, v.mode, v.detail, v.checked_at FROM verification_attempts v JOIN applications a ON a.id=v.application_id WHERE v.status IN ('verification_pending','source_unavailable','manual_review_required')" + scope + " ORDER BY v.id DESC", params).fetchall()
    escalation_rows = conn.execute("SELECT a.id AS application_id, a.reference, a.organization_name, a.escalation_level, a.escalation_reason, a.escalated_at, a.escalated_by, a.assigned_to FROM applications a WHERE a.escalated_at IS NOT NULL" + scope + " ORDER BY a.escalated_at DESC", params).fetchall()
    conn.close()
    items = [{"type": "finding", **dict(item)} for item in finding_rows]
    items.extend({"type": "clarification", **dict(item)} for item in clarification_rows)
    items.extend({
        "type": "verification",
        "application_id": item["application_id"],
        "reference": item["reference"],
        "organization_name": item["organization_name"],
        "verification_id": item["verification_id"],
        "status": item["status"],
        "mode": item["mode"],
        "severity": "medium",
        "title": f"{VERIFICATION_STATUS_LABELS.get(item['status'], item['status'])}: {item['method']}",
        "explanation": item["detail"],
        "message": item["detail"],
        "checked_at": item["checked_at"],
    } for item in pending_verifications)
    escalation_severity = {"senior_review": "medium", "committee_review": "high", "inspection_required": "critical"}
    items.extend({
        "type": "escalation",
        "application_id": item["application_id"],
        "reference": item["reference"],
        "organization_name": item["organization_name"],
        "severity": escalation_severity.get(item["escalation_level"], "high"),
        "status": item["escalation_level"],
        "title": f"Escalated for {item['escalation_level'].replace('_', ' ')}",
        "explanation": item["escalation_reason"],
        "message": item["escalation_reason"],
        "escalated_at": item["escalated_at"],
        "assigned_to": item["assigned_to"],
    } for item in escalation_rows)
    return {"items": items, "count": len(items)}

@app.get("/api/operations/audit-retention")
def audit_retention(actor=Depends(user)):
    if actor["role"] != "admin": raise HTTPException(403, "Only rule administrators can view operational audit policy")
    conn = db()
    summary = conn.execute("SELECT count(*) AS count, min(created_at) AS oldest, sum(CASE WHEN event_hash IS NOT NULL THEN 1 ELSE 0 END) AS hash_covered FROM audit_events").fetchone()
    conn.close()
    return {
        "retention_days": AUDIT_RETENTION_DAYS,
        "policy_status": "demo_default_unapproved",
        "policy_scope": "audit_events_only",
        "automatic_deletion": False,
        "event_count": summary["count"],
        "oldest_event": summary["oldest"],
        "hash_covered_events": summary["hash_covered"] or 0,
    }

class RuleChange(BaseModel):
    rule_id: str
    enabled: bool

class RulePackDraftInput(BaseModel):
    changes: list[RuleChange] = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=8, max_length=500)
    effective_from: str | None = Field(default=None, pattern=r"^20\d{2}-\d{2}$")
    effective_to: str | None = Field(default=None, pattern=r"^20\d{2}-\d{2}$")
    review_cycles: list[str] = Field(default_factory=list, max_length=3)
    request_types: list[str] = Field(default_factory=list, max_length=3)
    source_quality: str = Field(default="unverified", pattern="^(official_primary|government_copy|secondary|synthetic|unverified)$")
    source_reference: str = Field(default="", max_length=500)

def rule_pack_snapshot(conn, review_cycles=None, request_types=None, effective_from=None, effective_to=None):
    # Scoped packs are overlays for their matching cases. Inherit the newest
    # matching published snapshot, but never copy a narrower unrelated scope.
    years = review_cycles or list(DEMO_REVIEW_CYCLES)
    request_type_scope = request_types or list(DEMO_REQUEST_TYPE_LABELS)
    versions = conn.execute("SELECT snapshot_json FROM rule_pack_versions WHERE status IN ('published','superseded') ORDER BY id DESC").fetchall()
    for version in versions:
        payload = json.loads(version["snapshot_json"])
        scoped = any(key in payload for key in ("review_cycles", "request_types", "effective_from", "effective_to"))
        version_years = payload.get("review_cycles", list(DEMO_REVIEW_CYCLES))
        version_request_types = payload.get("request_types", list(DEMO_REQUEST_TYPE_LABELS))
        covers_effective_range = ((not effective_from or not payload.get("effective_from") or payload["effective_from"] <= effective_from)
                                  and (not effective_to or not payload.get("effective_to") or payload["effective_to"] >= effective_to))
        if (not scoped or (set(years).issubset(version_years) and set(request_type_scope).issubset(version_request_types)
                           and covers_effective_range)):
            snapshot = payload.get("rules", payload)
            if all(rule["id"] in snapshot for rule in RULES if rule["executable"]): return snapshot
    saved = {row["rule_id"]: bool(row["enabled"]) for row in conn.execute("SELECT rule_id,enabled FROM rule_settings")}
    return {rule["id"]: bool(rule["executable"] and saved.get(rule["id"], rule["enabled"])) for rule in RULES if rule["executable"]}

def ensure_initial_rule_pack(conn):
    seed_baseline_rule_pack(conn)

@app.get("/api/rules")
def rules(actor=Depends(user)):
    conn = db()
    ensure_initial_rule_pack(conn)
    enabled = {row["rule_id"]: bool(row["enabled"]) for row in conn.execute("SELECT rule_id, enabled FROM rule_settings")}
    pack_versions = conn.execute("SELECT version,snapshot_json FROM rule_pack_versions WHERE status IN ('published','superseded') ORDER BY id").fetchall()
    for version in pack_versions:
        payload = json.loads(version["snapshot_json"])
        scope = payload if any(key in payload for key in ("rules", "review_cycles", "request_types")) else {"rules": payload}
        for rule_id, is_enabled in scope.get("rules", {}).items():
            if not scope.get("review_cycles") and not scope.get("request_types"):
                enabled[rule_id] = bool(is_enabled)
                enabled[f"{rule_id}__pack_version"] = version["version"]
    versions = []
    for row in conn.execute("SELECT id,version,status,change_reason,created_by,created_at,approved_by,published_at,snapshot_json FROM rule_pack_versions ORDER BY id DESC LIMIT 20"):
        version = dict(row)
        version["scope"] = json.loads(version.pop("snapshot_json"))
        versions.append(version)
    conn.commit(); conn.close()
    items = []
    for source in RULES:
        item = dict(source)
        item["enabled"] = bool(source["executable"] and enabled.get(source["id"], source["enabled"]))
        item["latest_pack_version"] = enabled.get(f"{source['id']}__pack_version", RULE_PACK["version"])
        items.append(item)
    return {"rule_pack": RULE_PACK, "rules": items, "versions": versions}

@app.post("/api/rule-packs/drafts")
def create_rule_pack_draft(body: RulePackDraftInput, actor=Depends(user)):
    if actor["role"] != "admin": raise HTTPException(403, "Only rule administrators can propose a rule-pack version")
    if len({change.rule_id for change in body.changes}) != len(body.changes): raise HTTPException(400, "Each rule may appear only once in a change set")
    if any(change.rule_id not in RULE_BY_ID or not RULE_BY_ID[change.rule_id]["executable"] for change in body.changes): raise HTTPException(400, "Change set includes an unknown or reference-only rule")
    if body.effective_from and body.effective_to and body.effective_to < body.effective_from:
        raise HTTPException(400, "Effective end must not be earlier than effective start")
    if any(year not in DEMO_REVIEW_CYCLES for year in body.review_cycles): raise HTTPException(400, "Draft includes an unsupported review cycle")
    if any(request_type not in CHECKLIST for request_type in body.request_types): raise HTTPException(400, "Draft includes an unsupported request type")
    if body.effective_from and body.review_cycles and max(body.review_cycles) < body.effective_from:
        raise HTTPException(400, "Selected review cycles do not overlap the effective period")
    if body.effective_to and body.review_cycles and min(body.review_cycles) > body.effective_to:
        raise HTTPException(400, "Selected review cycles do not overlap the effective period")
    conn = db(); ensure_initial_rule_pack(conn)
    selected_years = body.review_cycles or list(DEMO_REVIEW_CYCLES)
    selected_request_types = body.request_types or list(DEMO_REQUEST_TYPE_LABELS)
    snapshot = rule_pack_snapshot(conn, selected_years, selected_request_types, body.effective_from, body.effective_to)
    snapshot.update({change.rule_id: change.enabled for change in body.changes})
    created_at = now()
    scope = {"rules": snapshot, "effective_from": body.effective_from, "effective_to": body.effective_to,
             "review_cycles": selected_years,
             "request_types": selected_request_types,
             "source_quality": body.source_quality, "source_reference": body.source_reference.strip()}
    row_id = insert_id(conn, "INSERT INTO rule_pack_versions(version,snapshot_json,status,change_reason,created_by,created_at) VALUES(?,?,?,?,?,?)", ("draft", json.dumps(scope, sort_keys=True), "draft", body.reason.strip(), actor["email"], created_at))
    conn.commit(); conn.close()
    return {"id": row_id, "version": f"draft-{row_id}", "status": "draft", "created_by": actor["email"], "created_at": created_at, "snapshot": scope, "source_quality": body.source_quality, "source_reference": body.source_reference.strip()}

@app.post("/api/rule-packs/{version_id}/publish")
def publish_rule_pack(version_id: int, actor=Depends(user)):
    if actor["role"] != "admin": raise HTTPException(403, "Only rule administrators can publish a rule pack")
    conn = db(); draft = conn.execute("SELECT * FROM rule_pack_versions WHERE id=?", (version_id,)).fetchone()
    if not draft: conn.close(); raise HTTPException(404, "Rule-pack version not found")
    if not conn.execute("SELECT 1 FROM rule_pack_versions WHERE id<>? AND status IN ('published','superseded') AND created_by<>? LIMIT 1", (version_id, actor["email"])).fetchone():
        conn.close(); raise HTTPException(409, "Independent approval requires a second provisioned rule administrator")
    if draft["status"] != "draft": conn.close(); raise HTTPException(409, "Only draft rule-pack versions can be published")
    if draft["created_by"] == actor["email"]: conn.close(); raise HTTPException(403, "A different rule administrator must publish this change")
    payload = json.loads(draft["snapshot_json"])
    snapshot = payload.get("rules", payload)
    if not isinstance(snapshot, dict) or any(rule["id"] not in snapshot for rule in RULES if rule["executable"]):
        conn.close(); raise HTTPException(400, "Draft snapshot must preserve every executable rule entry")
    published_version = f"{RULE_PACK['version']}+{version_id}"
    conn.execute("UPDATE rule_pack_versions SET version=?,status='published',approved_by=?,published_at=? WHERE id=?", (published_version, actor["email"], now(), version_id))
    conn.execute("UPDATE rule_pack_versions SET status='superseded' WHERE status='published' AND id<>?", (version_id,))
    conn.commit(); conn.close()
    return {"id": version_id, "version": published_version, "status": "published", "approved_by": actor["email"], "effective_from": payload.get("effective_from"), "effective_to": payload.get("effective_to"), "review_cycles": payload.get("review_cycles"), "request_types": payload.get("request_types"), "source_quality": payload.get("source_quality", "unverified"), "source_reference": payload.get("source_reference", "")}

@app.get("/api/applications/{application_id}/report")
def report(application_id: int, actor=Depends(user)):
    conn = db(); row = application_or_404(conn, application_id, actor); case = serialize_application(conn, row); conn.close()
    report_data = {"report_type": "Decision-support report", "generated_at": now(), "rule_pack": case["rule_pack"], "case": case, "disclaimer": "Automated results are advisory and not an approval decision."}
    if actor["role"] in {"reviewer", "admin"}:
        report_data["facility_reviews"] = list_facility_reviews(application_id, actor)["items"]
    return report_data

@app.get("/api/applications/{application_id}/audit/verify")
def verify_audit_chain(application_id: int, actor=Depends(user)):
    if actor["role"] != "admin": raise HTTPException(403, "Only administrators can verify audit integrity")
    conn = db(); application_or_404(conn, application_id, actor)
    rows = conn.execute("SELECT * FROM audit_events WHERE application_id=? ORDER BY id", (application_id,)).fetchall(); conn.close()
    previous_hash, legacy_events, valid = "", 0, True
    for event in rows:
        if not event["event_hash"]:
            legacy_events += 1
            continue
        expected = audit_event_hash(previous_hash, application_id, event["actor"], event["action"], event["detail"], event["created_at"])
        if event["previous_hash"] != (previous_hash or None) or event["event_hash"] != expected: valid = False
        previous_hash = event["event_hash"]
    return {"application_id": application_id, "valid": valid, "verified_events": len(rows) - legacy_events, "legacy_events": legacy_events, "head_hash": previous_hash or None}

@app.get("/api/applications")
def applications(actor=Depends(user)):
    conn = db()
    sql = """
        SELECT a.*,
               (SELECT count(*) FROM findings f WHERE f.application_id = a.id AND f.status = 'open') AS open_findings_count,
               (SELECT count(*) FROM findings f WHERE f.application_id = a.id AND f.status = 'open' AND f.severity IN ('high','critical')) AS high_risk_count,
               (SELECT count(*) FROM documents d WHERE d.application_id = a.id) AS document_count
        FROM applications a
    """
    params = ()
    if actor["role"] == "submitter":
        sql += " WHERE a.organization_id = ?"
        params = (actor["organization_id"],)
    elif actor["role"] == "reviewer":
        sql += " WHERE a.assigned_to IS NULL OR lower(a.assigned_to)=lower(?)"
        params = (actor["email"],)
    rows = [dict(x) for x in conn.execute(sql + " ORDER BY a.id", params)]
    for item in rows:
        item["checks_run"] = checks_are_current(conn, item["id"])
        selected_pack, selected_scope = select_rule_pack(conn, item)
        item["rule_pack_version"] = selected_pack["version"] if selected_pack else None
        item["request_type_label"] = DEMO_REQUEST_TYPE_LABELS.get(item["request_type"], "Unmapped request_type")
    conn.close()
    return rows

class ApplicationInput(BaseModel):
    organization_name: str = Field(min_length=3, max_length=200)
    review_cycle: str = Field(pattern=r"^20\d{2}-\d{2}$")
    address: str = Field(default="", max_length=300)
    scope_name: str = Field(default="", max_length=160)
    requested_capacity: int | None = Field(default=None, ge=1, le=10_000)
    request_type: str = Field(default="licence_renewal")

@app.post("/api/applications")
def create_application(body: ApplicationInput, actor=Depends(user)):
    if actor["role"] != "submitter": raise HTTPException(403, "Only submitters can create applications")
    if body.request_type not in CHECKLIST: raise HTTPException(400, "Unknown request type")
    if body.review_cycle not in DEMO_REVIEW_CYCLES: raise HTTPException(400, "Review cycle is outside this rule pack's supported cycles")
    conn = db(); reference = f"SUB-{body.review_cycle.split('-')[0]}-{secrets.randbelow(9000)+1000}"
    application_id = insert_id(conn, "INSERT INTO applications(reference,organization_id,organization_name,request_type,review_cycle,status,created_at,address,scope_name,requested_capacity,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (reference, actor["organization_id"], body.organization_name.strip(), body.request_type, body.review_cycle, "draft", now(), body.address.strip(), body.scope_name.strip(), body.requested_capacity, now()))
    audit(conn, application_id, actor, "application_created", f"Created {reference}"); conn.commit(); result = {"id": application_id, "reference": reference}; conn.close(); return result

@app.get("/api/applications/{application_id}")
def application(application_id: int, actor=Depends(user)):
    conn = db(); row = application_or_404(conn, application_id, actor); result = serialize_application(conn, row); conn.close(); return result

@app.patch("/api/applications/{application_id}")
def update_application(application_id: int, body: ApplicationInput, actor=Depends(user)):
    if actor["role"] != "submitter": raise HTTPException(403, "Only submitters can edit application details")
    conn = db(); current = application_or_404(conn, application_id, actor)
    if current["status"] not in {"draft", "deficiencies_identified", "ready_for_review", "awaiting_rectification", "rectification_received"}:
        conn.close(); raise HTTPException(409, "Application details are locked after submission; request an authorized reopening")
    if current["status"] in {"awaiting_rectification", "rectification_received"} and (body.organization_name.strip() != current["organization_name"] or body.request_type != current["request_type"] or body.review_cycle != current["review_cycle"]):
        conn.close(); raise HTTPException(409, "Organization, request type and review cycle cannot be changed during rectification")
    if body.request_type not in CHECKLIST: conn.close(); raise HTTPException(400, "Unknown request type")
    if body.review_cycle not in DEMO_REVIEW_CYCLES: conn.close(); raise HTTPException(400, "Review cycle is outside this rule pack's supported cycles")
    next_status = current["status"] if current["status"] in {"awaiting_rectification", "rectification_received"} else "draft"
    conn.execute("UPDATE applications SET organization_name=?, request_type=?, review_cycle=?, address=?, scope_name=?, requested_capacity=?, updated_at=?, status=? WHERE id=?", (body.organization_name.strip(), body.request_type, body.review_cycle, body.address.strip(), body.scope_name.strip(), body.requested_capacity, now(), next_status, application_id))
    audit(conn, application_id, actor, "application_updated", "Submitter details updated"); conn.commit(); conn.close(); return {"ok": True}

@app.post("/api/applications/{application_id}/precheck")
def precheck(application_id: int, actor=Depends(user)):
    if actor["role"] not in {"submitter", "reviewer"}: raise HTTPException(403, "Only submitters or reviewers can run the advisory pre-check")
    conn = db(); case = application_or_404(conn, application_id, actor)
    if actor["role"] == "submitter" and case["status"] not in {"draft", "deficiencies_identified", "ready_for_review", "awaiting_rectification", "rectification_received"}:
        conn.close(); raise HTTPException(409, "Application is locked after submission; an authorized reviewer must reopen it before revalidation")
    if case["review_cycle"] not in DEMO_REVIEW_CYCLES: conn.close(); raise HTTPException(400, "No rule pack is mapped to this review cycle")
    if case["request_type"] not in CHECKLIST: conn.close(); raise HTTPException(400, "No demo checklist is mapped to this application request_type")
    from .services import select_rule_pack
    active_pack, pack_scope = select_rule_pack(conn, case)
    if active_pack is None:
        conn.close(); raise HTTPException(409, "No published rule pack covers this review-cycle/request-type scope")
    count = run_checks(conn, application_id)
    applied_version = active_pack["version"]
    conn.execute("UPDATE findings SET rule_version=? WHERE application_id=? AND status='open'", (applied_version, application_id))
    audit(conn, application_id, actor, "precheck_run", f"Generated {count} advisory finding(s) using {RULE_PACK['id']} v{applied_version} for {case['review_cycle']} / {case['request_type']}")
    conn.commit(); conn.close();    return {"finding_count": count, "rule_pack_id": RULE_PACK["id"], "rule_pack_version": applied_version, "review_cycle": case["review_cycle"], "request_type": case["request_type"], "checklist": CHECKLIST[case["request_type"]], "authoritative": False}

@app.post("/api/applications/{application_id}/submit")
def submit(application_id: int, actor=Depends(user)):
    if actor["role"] != "submitter": raise HTTPException(403, "Only submitters can submit applications")
    conn = db(); case = application_or_404(conn, application_id, actor)
    if case["status"] not in {"draft", "deficiencies_identified", "ready_for_review", "awaiting_rectification", "rectification_received"}:
        conn.close(); raise HTTPException(409, "Application has already been submitted for reviewer review")
    if case["review_cycle"] not in DEMO_REVIEW_CYCLES or case["request_type"] not in CHECKLIST:
        conn.close(); raise HTTPException(400, "No demo checklist and rule pack are mapped to this application")
    if conn.execute("SELECT 1 FROM clarifications WHERE application_id=? AND status='open' LIMIT 1", (application_id,)).fetchone():
        conn.close(); raise HTTPException(409, "Resolve or respond to all open clarification requests before resubmission")
    from .services import select_rule_pack
    active_pack, _ = select_rule_pack(conn, case)
    if active_pack is None:
        conn.close(); raise HTTPException(409, "No published rule pack covers this review-cycle/request-type scope")
    count = run_checks(conn, application_id)
    conn.execute("UPDATE findings SET rule_version=? WHERE application_id=? AND status='open'", (active_pack["version"], application_id))
    status = "submitted"; conn.execute("UPDATE applications SET status = ? WHERE id = ?", (status, application_id)); audit(conn, application_id, actor, "submitted", f"Submitted for reviewer review with {count} advisory finding(s) using {active_pack['version']}"); conn.commit(); conn.close(); return {"status": status, "finding_count": count, "rule_pack_version": active_pack["version"]}

@app.post("/api/applications/{application_id}/documents")
async def upload(application_id: int, document_type: str, replaces_document_id: int | None = None, file: UploadFile = File(...), actor=Depends(user)):
    if actor["role"] != "submitter": raise HTTPException(403, "Only submitters can upload")
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS: raise HTTPException(400, "Allowed formats: PDF, TXT, CSV, PNG, JPG, JPEG, MP4")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content or len(content) > MAX_UPLOAD_BYTES: raise HTTPException(400, "File is empty or exceeds the 5 MB prototype limit")
    if extension == ".pdf" and not content.startswith(b"%PDF-"): raise HTTPException(400, "The file extension says PDF but the file signature is not a PDF")
    if extension in {".png", ".jpg", ".jpeg"}:
        if document_type != "facility_declaration": raise HTTPException(400, "Image evidence is currently limited to the facility declaration request_type")
        valid_signature = (extension == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n")) or (extension in {".jpg", ".jpeg"} and content.startswith(b"\xff\xd8\xff"))
        if not valid_signature: raise HTTPException(400, "The file extension does not match a supported PNG or JPEG image")
    if extension == ".mp4":
        if document_type != "facility_declaration": raise HTTPException(400, "Video evidence is currently limited to the facility declaration request_type")
        from .media import has_mp4_container_signature
        if not has_mp4_container_signature(content): raise HTTPException(400, "The file extension says MP4 but the container structure is invalid")
    if extension in {".txt", ".csv"}:
        try: content.decode("utf-8")
        except UnicodeDecodeError: raise HTTPException(400, "Text and CSV uploads must be UTF-8 encoded")
    try: scan_result = scan(content)
    except MalwareScanUnavailable as exc: raise HTTPException(503, str(exc))
    if scan_result.status == "infected": raise HTTPException(422, "Upload rejected by malware scanning")
    conn = db(); case = application_or_404(conn, application_id, actor)
    if case["status"] not in {"draft", "deficiencies_identified", "ready_for_review", "awaiting_rectification", "rectification_received"}:
        conn.close(); raise HTTPException(409, "Uploads are locked after submission; an authorized reviewer must reopen the case")
    allowed_types = {item["type"] for item in CHECKLIST[case["request_type"]]}
    if document_type not in allowed_types: conn.close(); raise HTTPException(400, "This document type is not part of the selected application's checklist")
    if replaces_document_id is not None and case["status"] not in {"awaiting_rectification", "rectification_received"}:
        conn.close(); raise HTTPException(409, "Document replacement is permitted only during an reviewer-requested rectification")
    next_status = "rectification_received" if case["status"] in {"awaiting_rectification", "rectification_received"} else "draft"
    conn.execute("UPDATE applications SET status=?,updated_at=? WHERE id=?", (next_status, now(), application_id))
    if replaces_document_id:
        previous = conn.execute("SELECT * FROM documents WHERE id=? AND application_id=?", (replaces_document_id, application_id)).fetchone()
        if not previous or previous["document_type"] != document_type: conn.close(); raise HTTPException(400, "The replacement target must be the same document type in this application")
        if conn.execute("SELECT 1 FROM documents WHERE supersedes_document_id=?", (replaces_document_id,)).fetchone(): conn.close(); raise HTTPException(409, "This document version has already been replaced")
    digest = sha256(content); storage_name = f"{digest}{extension}"
    try:
        storage.put(storage_name, content)
    except StorageError as exc:
        conn.close()
        raise HTTPException(503, "Document storage is unavailable") from exc
    pages, confidence, error = extract_page_texts(file.filename or storage_name, content)
    document_id = insert_id(conn, "INSERT INTO documents(application_id,document_type,original_name,storage_name,hash,confidence,extraction_error,created_at,malware_scan_status,malware_scan_detail,scanned_at,supersedes_document_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (application_id, document_type, file.filename, storage_name, digest, confidence, error, now(), scan_result.status, scan_result.detail, now(), replaces_document_id))
    extracted = [{"field_name": item["field_name"], "raw_value": item["raw_value"], "normalized_value": item["normalized_value"],
                  "page": item["page"], "confidence": confidence, "source": "pattern", "quote": item.get("quote")}
                 for item in extract_fields_with_pages_and_quotes(pages)]
    analysis = await run_in_threadpool(ai.analyze, pages)
    known_fields = {item["field_name"] for item in extracted}
    for item in analysis["fields"]:
        if item["field_name"] not in known_fields:
            extracted.append(item)
            known_fields.add(item["field_name"])
    for item in extracted:
        conn.execute("INSERT INTO extracted_fields(document_id,field_name,raw_value,normalized_value,page,confidence,source,quote) VALUES(?,?,?,?,?,?,?,?)",
                     (document_id, item["field_name"], item["raw_value"], item["normalized_value"], item["page"], item["confidence"], item.get("source", "pattern"), item.get("quote")))
    conn.execute("INSERT INTO document_ai_analysis(document_id,status,suggested_type,similarity,model,detail,analyzed_at) VALUES(?,?,?,?,?,?,?)",
                 (document_id, analysis["status"], analysis["suggested_type"], analysis["similarity"], analysis["model"], analysis["detail"], now()))
    action = "document_replaced" if replaces_document_id else "document_uploaded"
    audit_detail = f"{document_type}: {file.filename}; local_ai={analysis['status']}; suggested_type={analysis['suggested_type'] or 'none'}; ai_fields={sum(1 for item in extracted if item.get('source') == 'local_ai_qa')}"
    audit(conn, application_id, actor, action, audit_detail); conn.commit(); conn.close(); return {"document_id": document_id, "confidence": confidence, "extraction_error": error, "page_references": [{"field": item["field_name"], "page": item["page"]} for item in extracted], "ai_analysis": analysis, "replaces_document_id": replaces_document_id}

class FieldCorrection(BaseModel):
    field_name: str = Field(pattern=r"^(organization_name|certificate_number|valid_until|scope_name|capacity)$")
    value: str = Field(min_length=1, max_length=200)

@app.patch("/api/documents/{document_id}/fields")
def correct_field(document_id: int, body: FieldCorrection, actor=Depends(user)):
    conn = db(); document = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if not document: raise HTTPException(404, "Document not found")
    application_or_404(conn, document["application_id"], actor)
    conn.execute("DELETE FROM extracted_fields WHERE document_id=? AND field_name=?", (document_id, body.field_name))
    conn.execute("INSERT INTO extracted_fields(document_id,field_name,raw_value,normalized_value,page,confidence,source,quote) VALUES(?,?,?,?,?,1.0,'human_corrected',?)", (document_id, body.field_name, body.value.strip(), body.value.strip(), 1, f"Manual correction: {body.value.strip()}"))
    audit(conn, document["application_id"], actor, "field_corrected", f"{body.field_name} confirmed manually for document #{document_id}"); conn.commit(); conn.close()
    return {"ok": True, "confidence": 1.0}

class ReviewInput(BaseModel):
    outcome: str = Field(pattern="^(accepted|dismissed)$")
    reason: str = Field(min_length=3, max_length=500)

@app.post("/api/findings/{finding_id}/review")
def review(finding_id: int, body: ReviewInput, actor=Depends(user)):
    if actor["role"] != "reviewer": raise HTTPException(403, "Only reviewers can review findings")
    conn = db(); finding = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if not finding: conn.close(); raise HTTPException(404, "Finding not found")
    if finding["status"] != "open": conn.close(); raise HTTPException(409, "This finding has already been reviewed")
    application = application_or_404(conn, finding["application_id"], actor)
    conn.execute("UPDATE findings SET status=?, decision_reason=?, reviewed_by=? WHERE id=?", (body.outcome, body.reason.strip(), actor["email"], finding_id))
    open_count = conn.execute("SELECT count(*) AS count FROM findings WHERE application_id=? AND status='open' AND id<>?", (finding["application_id"], finding_id)).fetchone()["count"]
    if not open_count and application["status"] == "submitted": conn.execute("UPDATE applications SET status='reviewed' WHERE id=?", (finding["application_id"],))
    audit(conn, finding["application_id"], actor, f"finding_{body.outcome}", f"Finding #{finding_id}: {body.reason.strip()}"); conn.commit(); conn.close(); return {"ok": True, "status": body.outcome, "reviewed_by": actor["email"], "reason": body.reason.strip(), "remaining_open_findings": open_count}

class ClarificationInput(BaseModel): message: str = Field(min_length=3, max_length=500)
@app.post("/api/applications/{application_id}/clarifications")
def clarification(application_id: int, body: ClarificationInput, actor=Depends(user)):
    if actor["role"] != "reviewer": raise HTTPException(403, "Only reviewers can request clarification")
    conn = db(); application_or_404(conn, application_id, actor)
    clarification_id = insert_id(conn, "INSERT INTO clarifications(application_id,message,requested_by,requested_at) VALUES(?,?,?,?)", (application_id, body.message.strip(), actor["email"], now()))
    conn.execute("UPDATE applications SET status='awaiting_rectification' WHERE id=?", (application_id,)); audit(conn, application_id, actor, "clarification_requested", body.message.strip()); conn.commit(); conn.close(); return {"ok": True, "clarification_id": clarification_id, "status": "awaiting_rectification"}

class ClarificationResponse(BaseModel): message: str = Field(min_length=3, max_length=1000)
@app.post("/api/clarifications/{clarification_id}/respond")
def respond_clarification(clarification_id: int, body: ClarificationResponse, actor=Depends(user)):
    if actor["role"] != "submitter": raise HTTPException(403, "Only submitters can respond to clarifications")
    conn = db(); item = conn.execute("SELECT * FROM clarifications WHERE id=?", (clarification_id,)).fetchone()
    if not item: conn.close(); raise HTTPException(404, "Clarification not found")
    case = application_or_404(conn, item["application_id"], actor)
    if case["status"] != "awaiting_rectification": conn.close(); raise HTTPException(409, "Rectification is not currently permitted for this application")
    if item["status"] != "open": conn.close(); raise HTTPException(409, "This clarification has already been answered")
    conn.execute("UPDATE clarifications SET response=?,responded_by=?,responded_at=?,status='responded' WHERE id=?", (body.message.strip(), actor["email"], now(), clarification_id))
    open_requests = conn.execute("SELECT count(*) AS count FROM clarifications WHERE application_id=? AND status='open' AND id<>?", (item["application_id"], clarification_id)).fetchone()["count"]
    if not open_requests:
        conn.execute("UPDATE applications SET status='rectification_received' WHERE id=?", (item["application_id"],))
    audit(conn, item["application_id"], actor, "clarification_responded", body.message.strip()); conn.commit(); conn.close(); return {"ok": True}

@app.get("/api/reviewers")
def list_reviewers(actor=Depends(user)):
    if actor["role"] not in {"reviewer", "admin"}: raise HTTPException(403, "Only reviewers can view the reviewer roster")
    conn = db()
    if actor["role"] == "admin":
        reviewers = [dict(row) for row in conn.execute("SELECT email, role, active FROM users WHERE role='reviewer' AND active=TRUE ORDER BY email")]
        scope = "Administrators can assign or reassign cases to any active reviewer."
    else:
        reviewers = [{"email": actor["email"], "role": "reviewer", "active": True}]
        scope = "Reviewers may claim unassigned cases or release their own assignments. Assigned cases are visible only to their assigned reviewer and administrators."
    conn.close()
    return {"reviewers": reviewers, "scope": scope}

class AssignmentInput(BaseModel):
    reviewer_email: str | None = Field(default=None, max_length=254)

@app.post("/api/applications/{application_id}/assign")
def assign_application(application_id: int, body: AssignmentInput, actor=Depends(user)):
    if actor["role"] not in {"reviewer", "admin"}: raise HTTPException(403, "Only reviewers or administrators can assign cases")
    conn = db(); application_or_404(conn, application_id, actor)
    assigned_to = None
    if body.reviewer_email and body.reviewer_email.strip():
        reviewer = conn.execute("SELECT email FROM users WHERE lower(email)=? AND role='reviewer' AND active=TRUE", (body.reviewer_email.strip().lower(),)).fetchone()
        if not reviewer:
            conn.close(); raise HTTPException(400, "Cases may be assigned only to an active reviewer account")
        assigned_to = reviewer["email"]
        if actor["role"] == "reviewer" and assigned_to.lower() != actor["email"].lower():
            conn.close(); raise HTTPException(403, "Reviewers may claim cases for themselves; only administrators may assign cases to another reviewer")
    conn.execute("UPDATE applications SET assigned_to=?, updated_at=? WHERE id=?", (assigned_to, now(), application_id))
    audit(conn, application_id, actor, "case_assigned" if assigned_to else "case_unassigned", f"Assigned to {assigned_to}" if assigned_to else "Assignment cleared")
    conn.commit(); conn.close()
    return {"ok": True, "assigned_to": assigned_to}

class EscalationInput(BaseModel):
    level: str = Field(pattern="^(senior_review|committee_review|inspection_required)$")
    reason: str = Field(min_length=5, max_length=500)

@app.post("/api/applications/{application_id}/escalate")
def escalate_application(application_id: int, body: EscalationInput, actor=Depends(user)):
    if actor["role"] not in {"reviewer", "admin"}: raise HTTPException(403, "Only reviewers or administrators can escalate cases")
    conn = db(); application_or_404(conn, application_id, actor)
    escalated_at = now()
    conn.execute("UPDATE applications SET escalation_level=?, escalation_reason=?, escalated_at=?, escalated_by=?, updated_at=? WHERE id=?",
                 (body.level, body.reason.strip(), escalated_at, actor["email"], escalated_at, application_id))
    audit(conn, application_id, actor, "escalated", f"{body.level}: {body.reason.strip()}")
    conn.commit(); conn.close()
    return {"ok": True, "escalation_level": body.level, "escalated_at": escalated_at, "escalated_by": actor["email"], "routed_to_alerts": True}

class InfrastructureReviewInput(BaseModel):
    document_id: int = Field(ge=1)
    outcome: str = Field(pattern="^(observed_no_issue|follow_up_required|inspection_recommended|unable_to_assess)$")
    note: str = Field(min_length=5, max_length=1000)

@app.get("/api/applications/{application_id}/facility-reviews")
def list_facility_reviews(application_id: int, actor=Depends(user)):
    if actor["role"] not in {"reviewer", "admin"}:
        raise HTTPException(403, "Infrastructure reviews are available to reviewers and administrators")
    conn = db(); application_or_404(conn, application_id, actor)
    reviews = [dict(row) for row in conn.execute(
        "SELECT r.*, d.original_name FROM facility_reviews r JOIN documents d ON d.id=r.document_id WHERE r.application_id=? ORDER BY r.id DESC",
        (application_id,),
    )]
    conn.close()
    return {"items": reviews, "count": len(reviews), "automated_assessment": False}

@app.post("/api/applications/{application_id}/facility-reviews")
def create_facility_review(application_id: int, body: InfrastructureReviewInput, actor=Depends(user)):
    if actor["role"] not in {"reviewer", "admin"}:
        raise HTTPException(403, "Only reviewers or administrators can record facility reviews")
    conn = db(); application_or_404(conn, application_id, actor)
    document = conn.execute(
        "SELECT d.* FROM documents d WHERE d.id=? AND d.application_id=? AND (lower(d.original_name) LIKE '%.png' OR lower(d.original_name) LIKE '%.jpg' OR lower(d.original_name) LIKE '%.jpeg' OR lower(d.original_name) LIKE '%.mp4') AND NOT EXISTS(SELECT 1 FROM documents newer WHERE newer.supersedes_document_id=d.id)",
        (body.document_id, application_id),
    ).fetchone()
    if not document:
        conn.close(); raise HTTPException(400, "Select a current PNG, JPEG, or MP4 facility evidence document in this application")
    if document["document_type"] != "facility_declaration":
        conn.close(); raise HTTPException(400, "The selected media is not classified as facility evidence")
    created_at = now()
    note = body.note.strip()
    if len(note) < 5:
        conn.close(); raise HTTPException(400, "Infrastructure review notes must contain at least five non-space characters")
    cursor = conn.execute(
        "INSERT INTO facility_reviews(application_id,document_id,reviewer,outcome,note,created_at) VALUES(?,?,?,?,?,?)",
        (application_id, body.document_id, actor["email"], body.outcome, note, created_at),
    )
    review_id = cursor.lastrowid
    audit(conn, application_id, actor, "facility_review_recorded", f"Image document #{body.document_id}: {body.outcome}; {note}")
    if body.outcome == "inspection_recommended":
        conn.execute(
            "UPDATE applications SET escalation_level='inspection_required', escalation_reason=?, escalated_at=?, escalated_by=?, updated_at=? WHERE id=?",
            (f"Infrastructure image review (document #{body.document_id}): {note}", created_at, actor["email"], created_at, application_id),
        )
        audit(conn, application_id, actor, "escalated", f"inspection_required: facility evidence review #{review_id}")
    conn.commit(); conn.close()
    return {"id": review_id, "application_id": application_id, "document_id": body.document_id,
            "reviewer": actor["email"], "outcome": body.outcome, "note": note, "created_at": created_at,
            "inspection_routed": body.outcome == "inspection_recommended",
            "automated_assessment": False}

@app.post("/api/applications/{application_id}/escalation/resolve")
def resolve_escalation(application_id: int, actor=Depends(user)):
    if actor["role"] not in {"reviewer", "admin"}: raise HTTPException(403, "Only reviewers or administrators can resolve escalations")
    conn = db(); case = application_or_404(conn, application_id, actor)
    if not case["escalated_at"]:
        conn.close(); raise HTTPException(409, "This case has no open escalation")
    resolved_at = now()
    conn.execute("UPDATE applications SET escalation_level=NULL, escalation_reason=NULL, escalated_at=NULL, escalated_by=NULL, updated_at=? WHERE id=?", (resolved_at, application_id))
    audit(conn, application_id, actor, "escalation_resolved", f"Resolved {case['escalation_level']} escalation raised by {case['escalated_by']}")
    conn.commit(); conn.close()
    return {"ok": True, "escalated": False, "resolved_at": resolved_at}

# Verification results distinguish verified, mismatch detected, expired,
# verification pending, source unavailable and manual review required.
VERIFICATION_STATUSES = ("verified", "mismatch", "expired", "verification_pending", "source_unavailable", "manual_review_required")
VERIFICATION_STATUS_LABELS = {
    "verified": "Verified against source",
    "mismatch": "Mismatch detected",
    "expired": "Expired",
    "verification_pending": "Verification pending",
    "source_unavailable": "Source unavailable",
    "manual_review_required": "Manual review required",
}
VERIFICATION_COMPARISON_STATUSES = {"verified", "mismatch", "expired"}
VERIFICATION_MANUAL_STATUSES = {"verification_pending", "source_unavailable", "manual_review_required"}

def verification_requires_manual(status: str) -> bool:
    """Automated verification that is incomplete or inconclusive must be routed to a person."""
    return status in VERIFICATION_MANUAL_STATUSES

def normalized_comparison(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())

class VerificationInput(BaseModel):
    document_id: int | None = None
    method: str = Field(min_length=3, max_length=120)
    status: str = Field(pattern="^(verified|mismatch|expired|verification_pending|source_unavailable|manual_review_required)$")
    source: str = Field(min_length=3, max_length=120)
    declared_value: str | None = Field(default=None, max_length=300)
    source_value: str | None = Field(default=None, max_length=300)
    detail: str = Field(min_length=3, max_length=500)

@app.get("/api/connectors")
def connectors(actor=Depends(user)):
    return [
        {"id": "source_reference_demo", "name": "External source connector", "mode": "simulated", "status": "configured", "scope": "Synthetic certificate numbers only"},
        {"id": "provider_api", "name": "Provider API connector", "mode": "external", "status": "credentials_required", "scope": "Requires an authorised integration and credentials"},
    ]

@app.post("/api/applications/{application_id}/verification/simulate")
def simulate_verification(application_id: int, actor=Depends(user)):
    if actor["role"] not in {"reviewer", "admin"}: raise HTTPException(403, "Only reviewers can initiate verification")
    conn = db(); application_or_404(conn, application_id, actor)
    document = conn.execute("SELECT d.id, f.raw_value FROM documents d LEFT JOIN extracted_fields f ON f.document_id=d.id AND f.field_name='certificate_number' WHERE d.application_id=? AND f.raw_value IS NOT NULL LIMIT 1", (application_id,)).fetchone()
    from .sources import SimulatedSourceVerifier
    result = SimulatedSourceVerifier().verify(document["raw_value"] if document else None)
    doc_id = document["id"] if document else None
    checked_at = now()
    conn.execute("INSERT INTO verification_attempts(application_id,document_id,method,status,mode,detail,checked_at,source) VALUES(?,?,?,?,?,?,?,?)", (application_id, doc_id, SimulatedSourceVerifier.name, result.status, result.mode, result.detail, checked_at, result.source))
    audit(conn, application_id, actor, "verification_simulated", result.detail)
    audit(conn, application_id, actor, "verification_manual_confirmation_required", "Simulated connector leaves verification pending; a person must confirm")
    conn.commit(); conn.close()
    return {"ok": True, "status": result.status, "mode": result.mode, "label": result.label, "checked_at": checked_at, "requires_manual_confirmation": result.requires_manual_confirmation}

@app.post("/api/applications/{application_id}/verification")
def record_verification(application_id: int, body: VerificationInput, actor=Depends(user)):
    if actor["role"] not in {"reviewer", "admin"}: raise HTTPException(403, "Only reviewers can record verification activity")
    declared, returned = (body.declared_value or "").strip(), (body.source_value or "").strip()
    if body.status in VERIFICATION_COMPARISON_STATUSES:
        if not declared or not returned:
            raise HTTPException(400, "A comparison result must record both the declared value and the value returned by the source")
        if body.status == "verified" and normalized_comparison(declared) != normalized_comparison(returned):
            raise HTTPException(400, "Recorded values differ; record this result as a mismatch instead of verified")
        if body.status == "mismatch" and normalized_comparison(declared) == normalized_comparison(returned):
            raise HTTPException(400, "Recorded values match; record this result as verified instead of mismatch")
    elif declared or returned:
        raise HTTPException(400, "Declared and source values are recorded only for verified, mismatch or expired results")
    conn = db(); application_or_404(conn, application_id, actor)
    if body.document_id:
        doc = conn.execute("SELECT id FROM documents WHERE id=? AND application_id=?", (body.document_id, application_id)).fetchone()
        if not doc: conn.close(); raise HTTPException(400, "The selected document does not belong to this application")
    checked_at = now()
    comparison = f"Declared: {declared} | Source: {returned}" if body.status in VERIFICATION_COMPARISON_STATUSES else "No comparison recorded"
    detail = f"{body.detail.strip()} [Source: {body.source.strip()} | {comparison}]"
    conn.execute("INSERT INTO verification_attempts(application_id,document_id,method,status,mode,detail,checked_at,source) VALUES(?,?,?,?,?,?,?,?)", (application_id, body.document_id, body.method.strip(), body.status, "manual", detail, checked_at, body.source.strip()))
    requires_manual = verification_requires_manual(body.status)
    if requires_manual:
        audit(conn, application_id, actor, "verification_manual_confirmation_required", f"{body.method.strip()} ({body.source.strip()}): {body.status}")
    audit(conn, application_id, actor, "verification_recorded", f"{body.method.strip()} ({body.source.strip()}): {body.status}")
    conn.commit(); conn.close()
    return {"ok": True, "status": body.status, "label": f"Reviewer-recorded — {VERIFICATION_STATUS_LABELS[body.status]}; source not machine-verified.", "mode": "manual", "source": body.source.strip(), "checked_at": checked_at, "requires_manual_confirmation": requires_manual}

@app.get("/api/documents/{document_id}/download")
def download(document_id: int, actor=Depends(user)):
    conn = db(); document = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if not document: raise HTTPException(404, "Document not found")
    application_or_404(conn, document["application_id"], actor); conn.close()
    try: content = storage.get(document["storage_name"])
    except StorageError: raise HTTPException(404, "Stored file unavailable")
    filename = Path(document["original_name"]).name
    return StreamingResponse(iter([content]), headers={"Content-Disposition": f'attachment; filename="{filename}"'}, media_type="application/octet-stream")

@app.get("/api/documents/{document_id}/image-preview")
def image_preview(document_id: int, actor=Depends(user)):
    conn = db(); document = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if not document:
        conn.close(); raise HTTPException(404, "Document not found")
    application_or_404(conn, document["application_id"], actor); conn.close()
    extension = Path(document["original_name"]).suffix.lower()
    image_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    if extension not in image_types: raise HTTPException(415, "Image preview is available only for PNG and JPEG evidence")
    try:
        content = storage.get(document["storage_name"])
    except StorageError:
        raise HTTPException(404, "Stored file unavailable")
    return StreamingResponse(iter([content]), media_type=image_types[extension], headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})

@app.get("/api/documents/{document_id}/video-preview")
def video_preview(document_id: int, actor=Depends(user)):
    conn = db(); document = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if not document:
        conn.close(); raise HTTPException(404, "Document not found")
    application_or_404(conn, document["application_id"], actor); conn.close()
    if Path(document["original_name"]).suffix.lower() != ".mp4":
        raise HTTPException(415, "Video preview is available only for MP4 evidence")
    try:
        content = storage.get(document["storage_name"])
    except StorageError:
        raise HTTPException(404, "Stored file unavailable")
    from .media import has_mp4_container_signature
    if not has_mp4_container_signature(content):
        raise HTTPException(415, "Stored file is not a supported MP4 container")
    return StreamingResponse(iter([content]), media_type="video/mp4", headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})

@app.get("/api/documents/{document_id}/integrity")
def document_integrity(document_id: int, actor=Depends(user)):
    conn = db(); document = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if not document:
        conn.close(); raise HTTPException(404, "Document not found")
    application_or_404(conn, document["application_id"], actor); conn.close()
    if not document["original_name"].lower().endswith(".pdf"):
        return {"document_id": document_id, "status": "not_applicable", "signature_fields": [],
                "detail": "Signature-field inspection applies only to PDFs. No issuer or authenticity verification was performed."}
    try:
        content = storage.get(document["storage_name"])
    except StorageError:
        raise HTTPException(404, "Stored file unavailable")
    from .services import inspect_pdf_signature_fields
    return {"document_id": document_id, **inspect_pdf_signature_fields(content)}

@app.get("/api/documents/{document_id}/versions")
def document_versions(document_id: int, actor=Depends(user)):
    conn = db(); document = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if not document: conn.close(); raise HTTPException(404, "Document not found")
    application_or_404(conn, document["application_id"], actor)
    versions = [dict(row) for row in conn.execute("SELECT * FROM documents WHERE application_id=? AND document_type=? ORDER BY id DESC", (document["application_id"], document["document_type"]))]
    conn.close(); return versions

@app.get("/api/documents/{document_id}/pages/{page}")
def document_page(document_id: int, page: int, actor=Depends(user)):
    conn = db(); document = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if not document: conn.close(); raise HTTPException(404, "Document not found")
    application_or_404(conn, document["application_id"], actor); conn.close()
    try: content = storage.get(document["storage_name"])
    except StorageError: raise HTTPException(404, "Stored file unavailable")
    pages, _, error = extract_page_texts(document["original_name"], content)
    if not pages or page < 1 or page > len(pages):
        raise HTTPException(404, "Requested page not found in document")
    return {
        "document_id": document_id,
        "page": page,
        "total_pages": len(pages),
        "original_name": document["original_name"],
        "document_type": document["document_type"],
        "text": pages[page - 1],
        "error": error,
    }


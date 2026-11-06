-- Upgrade existing PostgreSQL deployments to database-backed shared login limits.
-- Safe to apply once through the reviewed deployment migration process.
CREATE TABLE IF NOT EXISTS login_attempts (
    account_key TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL CHECK (attempts >= 0),
    window_started_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS login_attempts_updated_at_idx ON login_attempts(updated_at);

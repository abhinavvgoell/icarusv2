-- Add the versioned rule-pack registry to older PostgreSQL demo deployments.
-- New databases already receive this table from 001_initial.sql.
CREATE TABLE IF NOT EXISTS rule_pack_versions (
    id BIGSERIAL PRIMARY KEY,
    version TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','published','superseded')),
    change_reason TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    approved_by TEXT,
    published_at TIMESTAMPTZ
);

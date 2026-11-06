-- Add human-reviewed facility evidence records to existing PostgreSQL deployments.
CREATE TABLE IF NOT EXISTS facility_reviews (
    id BIGSERIAL PRIMARY KEY,
    application_id BIGINT NOT NULL REFERENCES applications(id),
    document_id BIGINT NOT NULL REFERENCES documents(id),
    reviewer TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('observed_no_issue','follow_up_required','inspection_recommended','unable_to_assess')),
    note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

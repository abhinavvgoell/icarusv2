-- AI suggestions are advisory metadata, separate from regulatory findings.
ALTER TABLE extracted_fields
    ADD COLUMN source TEXT NOT NULL DEFAULT 'pattern';

CREATE TABLE document_ai_analysis (
    document_id BIGINT PRIMARY KEY REFERENCES documents(id),
    status TEXT NOT NULL,
    suggested_type TEXT,
    similarity DOUBLE PRECISION,
    model TEXT,
    detail TEXT NOT NULL,
    analyzed_at TIMESTAMPTZ NOT NULL
);

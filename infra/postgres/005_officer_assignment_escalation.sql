-- Reviewer workload assignment and escalation to official intervention.
-- Assignment records ownership only; it does not change read scope by itself.
ALTER TABLE applications ADD COLUMN assigned_to TEXT;
ALTER TABLE applications ADD COLUMN escalation_level TEXT;
ALTER TABLE applications ADD COLUMN escalation_reason TEXT;
ALTER TABLE applications ADD COLUMN escalated_at TIMESTAMPTZ;
ALTER TABLE applications ADD COLUMN escalated_by TEXT;
CREATE INDEX applications_escalated_at_idx ON applications(escalated_at) WHERE escalated_at IS NOT NULL;
-- Authorized source consulted for a verification result, recorded alongside its detail.
ALTER TABLE verification_attempts ADD COLUMN source TEXT;
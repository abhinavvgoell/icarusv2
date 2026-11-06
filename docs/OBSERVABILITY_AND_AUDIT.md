# Observability and audit operations

Icarus writes JSON request-completion events to standard output. Every response includes `X-Request-ID`; callers may submit a safe `X-Request-ID` value to correlate their own logs. Do not log passwords, session tokens, uploaded document contents, or personally identifying data outside the regulated audit store.

Health endpoints:

- `/api/health/live` confirms the process is running.
- `/api/health/ready` confirms database and document-storage connectivity and, when scanning is configured, a live ClamAV PING/PONG response.

Use readiness for load-balancer routing and alert if it fails. Ship standard output to a centralized system with restricted access and retention that matches the organisation's policy.

Each newly written audit event links to the previous hashed event for its application. PostgreSQL appends lock the application row while extending the chain, preventing concurrent actions from creating competing next events. This makes alterations detectable when the audit chain is verified. Existing demo-era audit records are not retroactively changed. The local demo defaults to 2,555 days (seven years), shown to administrators through `/api/operations/audit-retention` with status `demo_default_unapproved` and scope `audit_events_only`. It is not a legal retention schedule and does not apply to uploaded application documents. The application never deletes audit events automatically. Any production retention period and deletion workflow must follow the relevant agency-approved schedule and be separately reviewed.

An administrator can verify a case chain at `/api/applications/{application_id}/audit/verify`. Verification reports legacy records separately because it does not rewrite historical data.

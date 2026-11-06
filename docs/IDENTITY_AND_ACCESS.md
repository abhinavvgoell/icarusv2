# Identity and access

Icarus has two authentication modes:

- `local` is for local development and demo accounts only.
- `oidc` is for production and delegates sign-in and MFA to an organisation's OpenID Connect provider.

For production, set `APP_AUTH_MODE=oidc`, configure the external source/client values in the deployment secret manager, and set a unique 32+ character `APP_SESSION_COOKIE_SECRET`. The provider must issue verified email and subject claims. Optionally restrict identities with `APP_OIDC_ALLOWED_DOMAINS`.

Icarus does not automatically create users from an identity-provider login. An administrator provisions each identity first using `POST /api/admin/users`; only active, provisioned accounts may sign in. Administrators can deactivate an account through `PATCH /api/admin/users/{id}`. Deactivation invalidates all of that user's active sessions while retaining historical application and audit records.

In the local demo, submitters can access only applications belonging to their organization. Reviewers see unassigned capacity cases and cases assigned to their account; they can claim or release their own assignments. Only administrators can route or reassign a case to another reviewer. The same scope is enforced on dashboard counts, alerts, case details, reports and finding review, not only on the list view. A 404 is returned for an assigned case outside an reviewer's scope to avoid disclosing its existence. This demo policy is not a complete production authorization matrix: production still needs approved organizational boundaries, jurisdiction and assignment rules, privileged-access review, and security testing.

Local-password sign-in attempts are counted atomically in the shared database by a SHA-256 digest of the normalized account identifier. The configured attempt threshold and time window apply across restarts and PostgreSQL-backed replicas; throttle counters are ephemeral and are not carried during SQLite-to-PostgreSQL migration. OIDC credential validation and MFA remain the identity provider's responsibility.

Configure MFA, password policy, account recovery and offboarding in the identity provider. Treat role assignments and organization scope as regulated access-control decisions; review them periodically and retain the relevant approval record.

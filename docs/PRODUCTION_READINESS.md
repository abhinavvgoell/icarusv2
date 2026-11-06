# Icarus production-readiness baseline

## Included in this iteration

- Server-side session expiry, session cleanup, password hashing and logout invalidation.
- Atomic per-account sign-in throttling stored in the shared database, so limits persist across restarts and app replicas.
- Configurable trusted hosts and browser origins through environment variables.
- Security headers for browser responses and HSTS when the request arrives over HTTPS.
- Non-root container image and persisted container volume for the current SQLite deployment.
- A private S3-compatible document-storage adapter with KMS-capable server-side encryption.
- A reviewed PostgreSQL target schema in `infra/postgres/001_initial.sql`.
- A fail-closed ClamAV upload-scanning integration for production configuration.
- Configurable upload-byte and PDF-page caps; documents over the page-processing cap are preserved with extraction marked for manual review.
- Structured request logs with correlation IDs, liveness/readiness probes and hash-linked audit events.
- OpenID Connect integration, production MFA enforcement through the identity provider, and administrator-controlled account deactivation.
- Production defaults to no demo-data bootstrap, rejects startup if demo bootstrap is enabled, and refuses databases containing synthetic `.demo` identities.
- OIDC accounts require an administrator to provision the exact identity-provider subject in advance; callback email claims alone cannot claim an unbound account, and `email_verified` must be explicitly true.
- Production startup now fails closed unless PostgreSQL, private S3 with an explicit KMS key, OIDC client and approved email domain, ClamAV, a strong session secret, production hostnames, and HTTPS origins are configured.
- Production startup also rejects the current provisional approval rule pack. It must remain blocked until the complete controlling amendments are verified from official sources and an approved, versioned rule pack replaces the demo rules.
- The demo now records immutable local rule-pack draft/publish snapshots with review-cycle effective range, cycle/request-type scope, source quality/reference, reason and independent administrator approval. Case selection honors the applicable scope while retaining superseded versions. This is a software governance mechanism only; it does not replace your organisation's policy approval process.

## Deploy the baseline

1. Create a production-specific `.env` from the example; never commit `.env`. Set `APP_ENV=production`, `APP_BOOTSTRAP_DEMO_DATA=false`, PostgreSQL `APP_DATABASE_URL`, `APP_STORAGE_BACKEND=s3` with an explicit `aws:kms` key, `APP_AUTH_MODE=oidc`, a secret-manager-provided session key, ClamAV, the approved OIDC domain, a public hostname, and HTTPS browser origins. Startup rejects incomplete settings. The example's demo/local settings are for development only.
2. Set the public hostname in `APP_ALLOWED_HOSTS` and public HTTPS URL in `APP_ALLOWED_ORIGINS`.
3. For a new database, apply `infra/postgres/001_initial.sql` followed by any pending numbered upgrades `002_shared_login_throttling.sql` through `007_infrastructure_reviews.sql` in order. Existing databases should apply only the outstanding upgrades. Place the service behind a TLS-terminating reverse proxy. Do not expose the development server publicly.
4. Publish and scan an immutable Icarus image, inject the required values through the deployment secret manager, and start with `docker compose -f docker-compose.production.yml up -d`. The production template binds to host loopback for a same-host TLS proxy, runs with a read-only root filesystem, drops Linux capabilities, and requires the production settings instead of inheriting development defaults.
5. Back up the managed PostgreSQL database and object storage according to the organisation's recovery objectives before upgrades.

## Before real organizational data

- Provision the managed PostgreSQL service, apply migrations and run a tested data migration.
- Database-backed sessions and login throttles are shared across service instances; add a dedicated distributed store only if operational scale or policy requires it.
- Move uploaded files to encrypted object storage, add malware scanning and configure retention/deletion policies.
- Provision the organisation's identity provider, set its MFA policy and approve a production authorization matrix covering reviewer assignment, organizational boundaries, jurisdiction and privileged access before go-live. The local demo enforces assignment-based reviewer case visibility, but that policy is not a substitute for this review.
- Verify every OIDC subject with the organisation's identity-provider administrator, then provision the matching email, role, organization scope, and exact subject in Icarus before the user signs in. Do not use first-login email matching as account proof.
- Start with a clean production database. The application will refuse production startup if synthetic `.demo` identities are present; do not copy the prototype database into production.
- Obtain and reconcile a complete official copy of any applicable 2026–27 amendments before publishing a non-provisional regulatory rule pack. Source-register IDs S37–S59 were never created per the project team; do not invent or restore them. Claims without traceable primary sources remain unverified. A third-party mirror or secondary summary is not sufficient to clear the regulatory gate.
- Add centralized logs, alerting, backups, vulnerability scanning and an incident-response process.
- Route JSON logs to centralized retention-managed logging, define alerts for readiness failures, malware scanner outages and authentication abuse, and establish a formal audit-data retention schedule.
- Obtain written authorization, data-access scope and credentials before enabling an external source integration; treat the bundled simulated connector as a placeholder only.
- No live external registry, verification API, QR validation, cryptographic PDF-signature validation, document tamper forensics or computer vision is included. The demo supports private PNG/JPEG/MP4 evidence, auditable reviewer review records, and human inspection recommendations routed to reviewer alerts. Those records are manual judgments, not AI findings or formal determinations; default 5 MB uploads limit video evidence to short clips.
- Build a labeled evaluation corpus and report extraction, rule precision/recall, false-positive rates, verification-status correctness and processing-time measurements before claiming accuracy or time saved.

This image is a deployment baseline, not certification that the system is ready to process production regulatory data.

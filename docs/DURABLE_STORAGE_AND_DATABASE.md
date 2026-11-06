# Durable storage and database migration

## Document storage

Set `APP_STORAGE_BACKEND=s3` to use an S3-compatible private bucket. Arbiter keeps only generated object keys in `documents.storage_name`; it never returns a public bucket URL. Each upload is written with server-side encryption, using KMS by default.

Required production environment values:

```text
APP_STORAGE_BACKEND=s3
APP_S3_BUCKET=private-arbiter-documents
APP_S3_REGION=ap-south-1
APP_S3_PREFIX=production
APP_S3_ENCRYPTION=aws:kms
APP_S3_KMS_KEY_ID=alias/arbiter-documents
```

Attach a least-privilege workload identity allowing `s3:ListBucket` on the selected bucket, `s3:GetObject` and `s3:PutObject` only in the selected prefix, and `kms:GenerateDataKey` plus `kms:Decrypt` for the configured customer-managed key. S3 authorizes `HeadBucket` through `s3:ListBucket`; there is no `s3:HeadBucket` IAM action. Block public bucket access, enable bucket versioning, access logging, and lifecycle policies outside this application.

Document object writes are create-only: Arbiter uses the S3 `If-None-Match: *` precondition and treats a repeat with identical bytes as idempotent while rejecting different bytes at an existing key. The selected provider must support conditional `PutObject`; for Amazon S3, review its [conditional-write guidance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html) and consider enforcing the precondition in bucket policy. Rehearse this behavior against any S3-compatible service before production use.

## PostgreSQL target schema

`infra/postgres/001_initial.sql` defines the target schema for new databases; numbered files `002_shared_login_throttling.sql` through `007_infrastructure_reviews.sql` upgrade existing databases in order. Migrations 004–007 add source quotes, reviewer assignment/escalation metadata, the rule-pack version table, and human infrastructure image reviews. They are intentionally not applied automatically in production: provision the database separately, hold credentials in a secret manager, and run migrations through a reviewed CI/CD job. Arbiter startup checks that required shared tables and metadata columns exist and refuses an outdated PostgreSQL schema with a migration instruction.

Arbiter selects PostgreSQL at runtime when `APP_DATABASE_URL` is a PostgreSQL URL. It uses the same data-access operations as SQLite while translating parameter placeholders and generated IDs. The application never creates the PostgreSQL schema; apply the reviewed schema through the migration pipeline. Demo accounts and synthetic cases are seeded only when `APP_BOOTSTRAP_DEMO_DATA=true` and both the users and applications tables are empty. Never enable that option in production. For production, start with an approved clean database, set it to false, provision authorized identities and perform a separately tested data migration if required.

The one-time `scripts/migrate_sqlite_to_postgres.py` command migrates a SQLite database and verifies every source document's SHA-256 before copying it to the configured storage backend. It refuses synthetic `.demo` identities and non-empty PostgreSQL targets, preserves relational IDs, resets PostgreSQL sequences, and drops old sessions and ephemeral login-throttle windows so bearer credentials must be reissued without carrying local security state. Older SQLite sources without the optional throttle table remain migratable. The source must be quiesced before the cutover: the script reads a consistent SQLite snapshot, but writes committed after that snapshot are not included. Back up both the SQLite database and its upload directory, configure the destination backend and PostgreSQL URL, apply `infra/postgres/001_initial.sql` and any pending schema upgrades, run the script, compare row counts, and smoke-test login and document retrieval before directing users to the new service. A production migration should first be rehearsed against a restored copy; do not point it at the only copy of production data.

For a local integration exercise with synthetic data, use `docker compose -f docker-compose.staging.yml up --build`. It runs PostgreSQL, ClamAV, and Arbiter on `http://127.0.0.1:8002` by default; set `APP_STAGING_PORT` to override it. Its credentials and data are staging-only. The Compose stack mounts the target schema into PostgreSQL's first-run initialization directory, and the app seeds synthetic accounts only in the empty staging database. This is a local verification environment, not a production deployment recipe.

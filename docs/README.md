# Icarus documentation

Start with the [project README](../README.md) for the product overview and quick
start. Everything below goes one level deeper.

## Using it

| Document | What it covers |
| --- | --- |
| [DEMO_GUIDE.md](DEMO_GUIDE.md) | A full reviewer and submitter walkthrough against the seeded synthetic cases. |
| [POLICY_PACKS.md](POLICY_PACKS.md) | How checks are defined, scoped and published, and how to write your own pack. |
| [AI_DOCUMENT_ASSISTANCE.md](AI_DOCUMENT_ASSISTANCE.md) | The optional assistance pipeline, its guards and its limits. |
| [EVALUATION_STATUS.md](EVALUATION_STATUS.md) | What has actually been measured, on which data, and what has not. |
| [AI_BENCHMARK.md](AI_BENCHMARK.md) | The extraction benchmark protocol and the current synthetic results. |

## Operating it

| Document | What it covers |
| --- | --- |
| [IDENTITY_AND_ACCESS.md](IDENTITY_AND_ACCESS.md) | Roles, isolation, session handling and the OIDC path. |
| [DOCUMENT_INTEGRITY.md](DOCUMENT_INTEGRITY.md) | Hashing, duplicate detection, PDF signature cues and what they do not prove. |
| [MALWARE_SCANNING.md](MALWARE_SCANNING.md) | The optional ClamAV integration and its failure modes. |
| [DURABLE_STORAGE_AND_DATABASE.md](DURABLE_STORAGE_AND_DATABASE.md) | Local versus S3 storage and SQLite versus PostgreSQL. |
| [OBSERVABILITY_AND_AUDIT.md](OBSERVABILITY_AND_AUDIT.md) | Structured logs, request IDs, the audit chain and retention. |
| [DEPENDENCY_MANAGEMENT.md](DEPENDENCY_MANAGEMENT.md) | Pinned runtime requirements and the lock workflow. |
| [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) | Deployment baseline and the checks still required before real use. |

## House rules for these docs

- **Synthetic data only.** Every sample case, organization and document in this
  repository is invented. Do not paste real submissions into issues, tests or
  the demo database.
- **Advisory by design.** Nothing here produces a regulatory, legal or safety
  decision. Findings are reviewer prompts.
- **Measured, not asserted.** If a document claims accuracy, a speed-up or a
  verification, it must name the harness or test that produced the number, and
  the size of the set it was measured on.
- **No implied endorsement.** The example policy pack is configuration, not
  policy. It does not represent any authority, standard body or regulation.

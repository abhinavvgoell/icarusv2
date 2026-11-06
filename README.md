# Icarus v2

**Document compliance review with evidence-linked findings.**

Icarus v2 is a small, self-hosted review workspace for teams that check submitted
document bundles. A submitter files a case and uploads documents; Icarus
extracts the fields it can read, runs a versioned set of deterministic checks,
and turns every disagreement into a finding that points at the exact document,
page and quoted line that produced it. A human reviewer then accepts, rejects or
asks for rectification, and every step is written to an append-only audit trail.

It is decision *support*: no finding is a legal or regulatory decision, and the
bundled rule pack is an example configuration, not policy advice.

> Status: working prototype on synthetic data. The UI, API, checks, review
> workflow and exports are functional end to end; external source verification
> is simulated. See [Limitations](#limitations) before trusting any output.

---

## What it does

- **Cases with a clear lifecycle** — draft → submitted → pre-checked → ready for
  review → in review → decided, with per-role visibility and submitter isolation
  between organizations.
- **Upload and extraction** — PDF, TXT and CSV, plus PNG/JPEG/MP4 evidence.
  SHA-256 hashing, page-aware field extraction, OCR fallback for scanned PDFs,
  per-document confidence, and version-preserving replacement when a reviewer
  asks for a corrected document.
- **Nine deterministic checks** across completeness, name consistency,
  cross-document contradiction, scope item, capacity, expiry, exact duplicates
  and two extraction-quality signals. Every finding carries a rule ID, a
  severity, and evidence (document, page, quote).
- **Versioned rule packs** — checks are selected from an immutable, published
  pack with an effective review-cycle range and request-type scope, so editing a
  pack cannot silently change cases decided under an older one. Drafting and
  publishing require two different administrators.
- **Human review workflow** — finding disposition with a required reason,
  clarification requests and submitter responses, reviewer assignment and
  escalation, facility-evidence review, and simulated source verification.
- **Audit trail** — authenticated actions, hash-chained audit entries with a
  verification endpoint, and case reports exported as JSON or CSV.
- **UI** — a single-page vanilla-JavaScript app: dashboard, case list, case
  detail with a document sheet and highlighted evidence, rules browser,
  assessment view, verification records and audit log.

## Domain model

| Concept | Meaning |
| --- | --- |
| Organization | The entity a case belongs to (`ORG-1001`). Submitters only ever see their own. |
| Request type | `licence_renewal`, `scope_expansion` or `new_registration`. Each has its own required-document checklist. |
| Review cycle | Filing period such as `2026-27`; used to select the effective rule pack. |
| Scope name | The service or product line the case covers, e.g. `Bulk Storage`. |
| Capacity | The requested number of units and the previously sanctioned number. |
| Document types | `declaration`, `safety_certificate`, `prior_approval`, `staff_declaration`, `consent_letter`, `facility_declaration`. |
| Finding | One rule outcome with severity, evidence and a status (`open`, `accepted`, `dismissed`, `rectification_required`). |
| Rule pack | An immutable published set of enabled rules with applicability metadata. |

## Bundled checks (pack `ICARUS-DEFAULT` v2.0.0)

| Rule | What it flags |
| --- | --- |
| `DOCS-REQUIRED-001` | A document the checklist marks as required is missing. |
| `NAME-MATCH-001` | An extracted organization name differs from the case record. |
| `FIELD-CONFLICT-001` | Two documents state different values for the same field. |
| `SCOPE-MATCH-001` | The prior approval names a different scope item than the case. |
| `CAPACITY-MATCH-001` | Requested capacity exceeds the previously sanctioned capacity. |
| `EXPIRY-001` | A printed validity date has already passed. |
| `DUPLICATE-001` | Two files in the same case are byte-identical. |
| `EXTRACT-001` | Extraction or optional assistance could not produce a reliable result. |
| `EXTRACT-002` | Extraction confidence is below the review threshold. |

`POLICY-REF-001` and `POLICY-REF-002` are reference-only entries that document
where policy text would live; they never execute.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000>. On first start Icarus creates `data/prototype.db`
and seeds three synthetic cases when `APP_BOOTSTRAP_DEMO_DATA=true` (the
development default).

### Run it from GitHub (Codespaces)

The repository ships a dev container, so it can run in the cloud with no local
install: open <https://codespaces.new/abhinavvgoell/icarusv2>, wait for the image
to build, and forwarded port 8000 opens the sign-in page. The container starts
uvicorn automatically, widens `APP_ALLOWED_HOSTS` to `*.app.github.dev` so the
forwarded URL is accepted, and seeds the same synthetic demo data.

GitHub Pages cannot host this project: the UI is served by FastAPI and every
screen after sign-in calls the API, so a static-only host would show a login form
that cannot authenticate. For a public instance, build the container
(`docker compose up --build`) and run it on any host that keeps a Python process
and a writable volume.

Optional extras:

- **Local AI assistance** — `.\\scripts\\download_ai_models.ps1` installs the
  ONNX classifier and extractor. Without it, uploads still extract through
  deterministic patterns and the AI status shows as unavailable; nothing else
  changes.
- **OCR** — install Tesseract and set `APP_OCR_LANGUAGE` to read scanned PDFs.
- **PostgreSQL** — apply `infra/postgres/*.sql` and set `APP_DATABASE_URL`.

### Demo accounts

| Role | Login | Password |
| --- | --- | --- |
| Submitter (Northwind Logistics Ltd) | `submitter@northwind.demo` | `Submitter@123` |
| Submitter (Cascade Chemicals Ltd) | `submitter@cascade.demo` | `Submitter@123` |
| Reviewer | `reviewer@icarus.demo` | `Reviewer@123` |
| Administrator | `admin@icarus.demo` | `Admin@123` |

All demo data is fictional. Do not upload real personal or commercial documents
into the default development environment.

## Five-minute walkthrough

1. Sign in as the reviewer and open **Cases** — three seeded cases with a mix of
   open findings.
2. Open `SUB-2026-1002` (Cascade Chemicals) and inspect a finding: the document
   sheet opens at the cited page with the extracted value highlighted.
3. Accept or dismiss the finding with a reason; the decision appears in the audit
   log immediately.
4. Run **Assessment** to see per-rule status for the case, then export the case
   report as JSON.
5. Switch to a submitter login, submit a case and upload one of the sample files
   in `docs/demo_data/` to watch extraction and the pre-check run live.

Longer walkthrough: [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md). Rule pack
mechanics: [docs/POLICY_PACKS.md](docs/POLICY_PACKS.md).

## Architecture

```
static/            single-page UI (app.js, styles.css, index.html)
app/main.py        FastAPI app: auth, cases, documents, review, rules, audit
app/services.py    extraction, storage, run_checks, rule-pack selection
app/rules.py       policy pack, checklists, rule metadata, normalization
app/ai.py          optional local ONNX assistance and plausibility guards
app/llm.py         optional external provider (explicit opt-in)
app/sources.py     simulated external source verifier
app/database.py    SQLite/PostgreSQL connection helpers
app/observability.py  structured logs and request IDs
infra/postgres/    schema for the PostgreSQL deployment path
tests/             end-to-end API tests against a temporary database
scripts/           evaluation harnesses, demo-PDF generator, model download
```

Design notes worth knowing:

- Checks read only from the database, so a pre-check is reproducible and cheap.
- Findings store their evidence at creation time; later document versions never
  rewrite history.
- Extraction never blocks a case: an unreadable file produces a review signal,
  not an error.
- Optional assistance is treated as an environment capability. If the models are
  not installed, uploads record `manual_review` but no document finding is
  raised — a missing optional install is not a defect in the submission.

## Tests and evaluation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Current state: **72 passed** with the optional models installed, or **69 passed,
3 skipped** without them — the skips are the on-device model acceptance tests,
which need `scripts/download_ai_models.ps1` first.

Two harnesses measure the parts that are easy to get quietly wrong:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_rule_engine.py --json-out .eval-out.json
.\.venv\Scripts\python.exe scripts\evaluate_ai_extraction.py
```

- **Rule harness** — 11 hand-labelled synthetic cases in
  `docs/demo_data/rule_evaluation_cases.json`, with expected *and* forbidden
  rules per case, so a check that fires too often is caught as well as one that
  misses. Latest run: 12 expected rule instances, precision 1.0, recall 1.0.
- **Extraction harness** — 10 synthetic documents, 29 labelled fields, scored on
  exact value **and** page. Latest run: 29/29 with deterministic patterns.

Both sets are small, synthetic and authored alongside the code. They are
regression checks, not evidence of real-world accuracy; treat the reported
numbers accordingly.

## Configuration

Everything is environment-driven; see [`.env.example`](.env.example) for the full
list. The settings that matter most:

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_DATABASE_PATH` | `data/prototype.db` | SQLite file. Use `APP_DATABASE_URL` for PostgreSQL. |
| `APP_BOOTSTRAP_DEMO_DATA` | `true` | Seeds synthetic cases on first start. Set `false` outside demos. |
| `APP_MAX_UPLOAD_BYTES` | `5242880` | Upload cap (5 MB). |
| `APP_MAX_PDF_PAGES` | `25` | Larger PDFs skip text extraction and are flagged for review. |
| `APP_AI_ENABLED` / `APP_AI_PROVIDER` | `true` / `local` | Optional assistance. `openai` sends document text externally and needs explicit opt-in plus `OPENAI_API_KEY`. |
| `APP_AUTH_MODE` | `local` | `oidc` enables the OpenID Connect login path. |
| `APP_STORAGE_BACKEND` | `local` | `s3` uses a private bucket. |
| `APP_MALWARE_SCAN_MODE` | `disabled` | `clamav` scans uploads before they are stored. |
| `APP_ALLOWED_HOSTS` / `APP_ALLOWED_ORIGINS` | loopback | Host and CORS allow-lists. |
| `APP_AUDIT_RETENTION_DAYS` | `2555` | Audit retention window reported by the operations endpoint. |

`APP_SESSION_COOKIE_SECRET` is required in production; the app refuses to start
with `APP_ENV=production` and a short or missing secret.

## Limitations

- **Advisory only.** Findings are review prompts; nothing here decides a case.
- **Simulated verification.** `app/sources.py` fabricates source responses and
  says so in every record. There is no live registry, signature, QR or issuer
  check.
- **No forensics.** Duplicate detection is exact-hash only. There is no image or
  metadata tampering analysis, and no computer-vision review of facility media —
  a person reviews that evidence and the outcome is recorded.
- **Example policy.** `ICARUS-DEFAULT` is a generic sample pack. Real use needs
  your own sources, clauses and an approved pack.
- **Demo authentication.** Local password login is for development; use the OIDC
  path with a real identity provider for anything shared.
- **Small evaluation sets.** See above — treat the metrics as regression signals.

## Documentation

- [docs/README.md](docs/README.md) — documentation index
- [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md) — end-to-end walkthrough
- [docs/POLICY_PACKS.md](docs/POLICY_PACKS.md) — how checks, packs and scoping work
- [docs/EVALUATION_STATUS.md](docs/EVALUATION_STATUS.md) — what has been measured
- [docs/AI_DOCUMENT_ASSISTANCE.md](docs/AI_DOCUMENT_ASSISTANCE.md) — assistance design and limits
- [docs/DOCUMENT_INTEGRITY.md](docs/DOCUMENT_INTEGRITY.md), [docs/MALWARE_SCANNING.md](docs/MALWARE_SCANNING.md)
- [docs/IDENTITY_AND_ACCESS.md](docs/IDENTITY_AND_ACCESS.md), [docs/OBSERVABILITY_AND_AUDIT.md](docs/OBSERVABILITY_AND_AUDIT.md)
- [docs/DURABLE_STORAGE_AND_DATABASE.md](docs/DURABLE_STORAGE_AND_DATABASE.md), [docs/DEPENDENCY_MANAGEMENT.md](docs/DEPENDENCY_MANAGEMENT.md)
- [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) — deployment baseline, not certification

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: run the tests, keep
sample data synthetic, and do not add a claim to this README that a test or a
measurement does not back.

## License

No license file is included yet. Add one (for example MIT or Apache-2.0) before
publishing this repository, and update this section to match.

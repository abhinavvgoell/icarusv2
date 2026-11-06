# Contributing

Arbiter is a document-review prototype. Contributions are welcome as long as
they keep it honest: synthetic data only, advisory findings, and no claim that a
test or measurement does not support.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

`requirements-runtime.lock` is the pinned runtime set; the PowerShell helpers in
`scripts/` regenerate it. If you change a dependency, update
`requirements-runtime.in`, regenerate the lock with
`scripts/compile_runtime_lock.ps1`, and say why in the pull request.

The optional on-device models are installed with
`.\\scripts\\download_ai_models.ps1`. Tests that need them skip when they are
absent, so a plain checkout still runs green.

## Ground rules

- **Synthetic data only.** Never add a real certificate, person, organization or
  address to fixtures, tests, screenshots or issues. Invented names in the
  `*.demo` style are fine.
- **No policy claims.** The bundled rule pack is example configuration. Do not
  describe a check as a legal, regulatory or safety requirement, and do not add
  a citation that has not been verified against a primary source.
- **No implied verification.** Nothing in this project may state or imply that a
  document was authenticated, a signature cryptographically verified, or an
  external register contacted.
- **Evidence over assertion.** Do not add a README or docs claim about accuracy,
  coverage or speed unless a harness in `scripts/` produces the number and the
  document states the size and nature of the set.

## Changing checks

1. Update the rule metadata in `app/rules.py` (ID, name, scope, logic, source,
   applicability) and, if needed, the check itself in `run_checks` in
   `app/services.py`.
2. Add a case to `docs/demo_data/rule_evaluation_cases.json` that should trigger
   it, **and** at least one that must not (`forbidden_rules`). A check with only
   a positive case is a check nobody tested for noise.
3. Run `scripts/evaluate_rule_engine.py --json-out .eval-out.json` and confirm no
   regression in precision or recall.
4. Keep rule IDs stable once a pack is published — decide under an old pack must
   stay explainable.

## Changing extraction

`scripts/evaluate_ai_extraction.py` scores the deterministic extractor per field
against `docs/demo_data/ai_extraction_benchmark.json` on both value and page.
If you add a pattern, add a labelled document for it; if you loosen a pattern,
check that the existing cases still pass and that nothing new appears as an
extraction error.

## Database and API changes

- Schema changes belong in `infra/postgres/` as a new numbered file, with the
  matching SQLite statement in `init_db` in `app/main.py`. Do not edit an applied
  migration.
- Any new endpoint that reads or writes case data must enforce role and
  organization scope the same way the existing ones do, and must write an audit
  entry for state changes.
- Responses that include case content must set `Cache-Control: no-store`.

## Frontend changes

The UI is a single-page vanilla-JavaScript app (`static/app.js`,
`static/styles.css`). Run `node --check static/app.js` before committing, bump
the `?v=` cache-busting query in `static/index.html` when you change either file,
and keep evidence views (document, page, quote) working — a finding without
evidence is not reviewable.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Add a test for behaviour you added, and fix the cause of a failing test rather
than relaxing the assertion. Report anything you could not run, including skipped
model tests, in the pull request.

## Documentation

- `README.md` — product overview, quick start, limitations. Keep the limitations
  section current; it is the most-read part of the file.
- `docs/` — topic documents, indexed in `docs/README.md`.
- `docs/DEMO_GUIDE.md` — update it when a workflow or seeded case changes.

## Committing

Small, focused changes with a message that says why. Do not commit generated
artifacts (`data/`, `server.log`, `.eval-out.json`), model binaries, or anything
containing credentials.

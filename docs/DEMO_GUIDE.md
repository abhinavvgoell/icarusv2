# Demo guide

A 10–15 minute walkthrough of everything Arbiter does, using only the synthetic
data the application seeds itself. No external services are contacted at any
point.

## Before you start

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Open <http://127.0.0.1:8000>. If you have run the app before and want the exact
state described here, stop the server, delete `data/prototype.db` and start it
again — the seed only runs against an empty database.

| Login | Password | Role |
| --- | --- | --- |
| `reviewer@arbiter.demo` | `Reviewer@123` | Reviewer |
| `submitter@northwind.demo` | `Submitter@123` | Submitter, Northwind Logistics Ltd |
| `submitter@cascade.demo` | `Submitter@123` | Submitter, Cascade Chemicals Ltd |
| `admin@arbiter.demo` | `Admin@123` | Administrator |

## What is seeded

| Case | Organization | Request type | What it demonstrates |
| --- | --- | --- | --- |
| `SUB-2026-1001` | Northwind Logistics Ltd | `licence_renewal` | A clean, complete renewal: three documents, consistent names, valid dates. |
| `SUB-2026-1002` | Cascade Chemicals Ltd | `licence_renewal` | Real review work: an expired safety certificate, a name mismatch and a missing prior approval. |
| `SUB-2026-1003` | Meridian Waste Services | `licence_renewal` | Processing edge cases: a byte-identical duplicate and an unreadable PDF. |

## 1. Overview

Sign in as the reviewer. The **Overview** page shows total cases, open findings,
cases awaiting review, completed dispositions, escalations and assignments, with
a warning that "absence of findings is not a pass" for cases that were never
pre-checked.

Try **Export report** — it downloads the dashboard JSON, including the in-scope
alert list and the status/request-type/review-cycle breakdowns.

## 2. Applications

Open **Applications**. Filters cover search, status, request type and review
cycle. As a submitter you would only ever see your own organization's cases here;
as a reviewer you see assigned cases plus unassigned intake.

Open `SUB-2026-1002`. The case sheet shows the case header, the request-specific
checklist, the document list with extraction confidence, and the open findings.

## 3. Document review and evidence

Click a finding, or switch to **Document review**. The document sheet renders the
extracted text with the cited value highlighted and shows the page it came from,
so a reviewer never has to trust a finding without looking at the source.

Useful things to check on `SUB-2026-1002`:

- **Name mismatch** — the declaration says *Cascade Chemicals Ltd*, the safety
  certificate says *Cascade Chemical Works*. The finding quotes both.
- **Expired certificate** — the safety certificate's printed validity date is
  `2025-03-31`, before the review date.
- **Missing document** — the checklist requires a prior approval for a renewal,
  and none was uploaded.

On `SUB-2026-1003`, the declaration and its copy share a hash (duplicate finding)
and the prior approval is an unreadable file (`EXTRACT-001`).

## 4. Disposition

Open a finding and record a decision with a reason. The status moves to
accepted, dismissed or rectification-required, the case status follows, and an
audit entry is written in the same transaction.

Delivery decisions are deliberately unavailable while findings are open: the API
rejects them rather than letting a reviewer skip evidence.

## 5. Assessment

**Assessment** shows the case rule by rule: met, not met, not applicable, or not
evaluated. This is the view to use when explaining *why* a case is not ready.
**Export case report** writes the same information as JSON.

## 6. Source checks

**Source checks** lists verification attempts per document. The statuses —
verified, mismatch, expired, verification pending, source unavailable, manual
review required — are all recorded explicitly, and every simulated entry says so
in its detail text. Pending, unavailable and manual-review results are routed
into the reviewer alert queue.

Nothing here contacts an external register. Read
`app/sources.py` before assuming otherwise.

## 7. Rules & policies

**Rules & policies** shows the effective pack (`ARBITER-DEFAULT` v1.0.0) and every
rule with its source, clause, status and applicability. Note the wording: the
source is an example pack, not a citation, and the approval status says the pack
is configuration.

Administrators can draft a new pack version and publish it; publishing requires a
different administrator from the one who drafted it. See
[POLICY_PACKS.md](POLICY_PACKS.md).

## 8. Audit trail

Every action so far is listed with actor, action, detail and timestamp. Use the
verify action to check the hash chain over the case's entries — a modified or
missing entry breaks the chain and is reported.

## 9. The submitter side

Sign out and sign in as `submitter@northwind.demo`.

1. **New application** — pick a request type, review cycle, scope name and
   capacity.
2. Upload documents. `docs/demo_data/` contains ready-made samples:
   - `scenario_1_clean_renewal.txt` — a consistent renewal record
   - `scenario_4_expired_safety_cert.txt` — an expired validity date
   - `scenario_3_cross_contradiction_declaration.txt` and
     `scenario_3_cross_contradiction_safety.txt` — two documents that disagree
   - `scenario_5_duplicate_hash_a.txt` / `_b.txt` — byte-identical files
   - `scenario_6_capacity_mismatch.txt` — a sanctioned capacity below the request
   - `scenario_ocr_scanned_demo.pdf` — a three-page PDF whose last page is an
     image only, for the OCR path
3. Run the **pre-check**, then submit the case. Extraction, findings and the
   checklist all update immediately.

If a reviewer requests rectification, upload the corrected document as a
replacement: the earlier version stays visible in the document history and the
findings that referenced it keep pointing at what the reviewer actually saw.

## Interpreting what you see

- Findings are **review prompts with evidence**, not verdicts. A dismissed
  finding is a normal outcome.
- Absence of findings means the configured checks were silent — it is not a
  statement that a submission is compliant.
- Simulated verification is labelled simulated everywhere. Do not present it as
  a check against a real register.

# Scenario 2 - missing required document

## Overview

This scenario demonstrates `DOCS-REQUIRED-001`. The check reads the checklist for
the case's request type and flags any entry marked required that has no document
of that type. There is no upload file for this scenario: the finding exists
precisely because a document does not.

## What the check requires

| Request type | Required document types |
| --- | --- |
| `licence_renewal` | `declaration`, `safety_certificate`, `prior_approval` |
| `scope_expansion` | `declaration`, `prior_approval`, `staff_declaration` |
| `new_registration` | `declaration`, `consent_letter`, `facility_declaration` |

A document replaced by a newer version does not count as missing, and a
superseded version is not counted twice.

## Demo steps

1. Start the app on port 8000 and sign in as a submitter.
2. Create a case, or open a existing one, and upload **no** documents.
3. Run the pre-check (`POST /api/applications/{id}/precheck`).
4. Each required type produces one finding:
   - kind `compliance_check`
   - severity `high`
   - rule `DOCS-REQUIRED-001`
   - title `Missing: <document label>`
5. Upload one document and re-run the pre-check. That finding clears; the others
   remain.

## Why this scenario ships without a file

The scenario is reproduced by using a case with zero documents, or by working
with a document type the checklist requires and simply not uploading it. Nothing
has to be deleted to see it.

The rule evaluation harness covers the same case programmatically:
`docs/demo_data/rule_evaluation_cases.json` contains
`missing-required-documents`, which expects exactly one `DOCS-REQUIRED-001` and
forbids the other checks from firing.

## Evidence and traceability

A missing-document finding has no document to cite, so `document_id`, `evidence`
and `evidence_page` are empty:

```json
{
  "rule_id": "DOCS-REQUIRED-001",
  "rule_version": "Pack 1.0.0",
  "kind": "compliance_check",
  "severity": "high",
  "title": "Missing: Prior approval",
  "explanation": "The selected checklist marks this document as required for this request type.",
  "evidence": null,
  "evidence_page": null
}
```

The explanation names the checklist as the reason, so a reviewer can go to
**Rules & policies** and see exactly which configuration produced the finding.

> **Note:** the checklist is example configuration shipped with the demo, not a
> legal or regulatory requirement. Findings are review prompts, and a person
> makes the decision.

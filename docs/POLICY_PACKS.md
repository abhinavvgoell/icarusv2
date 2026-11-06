# Policy packs

A policy pack is the answer to one question: *which checks run, with which
metadata, for this kind of case, in this review cycle?* Everything a check needs
to be explainable — its ID, its source, its clause, its applicability — lives in
the pack, not in the check code.

## What a pack contains

`app/rules.py` holds the pack definition and the rule registry:

```python
RULE_PACK = {
    "id": "ARBITER-DEFAULT",
    "version": "1.0.0",
    "label": "Default example policy pack",
    "source": "Example policy pack bundled with Arbiter",
    "provisional": False,
    "authoritative": False,
    "review_cycles": ["2024-25", "2025-26", "2026-27"],
    "request_types": ["licence_renewal", "scope_expansion", "new_registration"],
    "approval_status": "Example configuration - not legal advice or regulatory approval",
    "notice": "...",
}
```

Each rule in `RULES` carries `id`, `name`, `scope`, `status`, `logic`,
`review_cycles`, `request_types`, optional `effective_from` / `effective_to`,
`source`, `source_version`, `clause`, `source_quality`, `approval_status`,
`verification_status` and `enabled`. `RULE_BY_ID` is a lookup built from that
list, and `GET /api/rules` exposes both the pack and the registry to the UI.

Checks are grouped by request type in `CHECKLIST`, which is what
`DOCS-REQUIRED-001` consults:

| Request type | Required documents |
| --- | --- |
| `licence_renewal` | `declaration`, `safety_certificate`, `prior_approval` |
| `scope_expansion` | `declaration`, `prior_approval`, `staff_declaration` |
| `new_registration` | `declaration`, `consent_letter`, `facility_declaration` |

## The bundled pack

`ARBITER-DEFAULT` v1.0.0 ships nine executable checks plus two reference-only
entries. Nothing in it is a quotation from a real instrument; the source fields
say so explicitly ("Arbiter example policy pack (not a real citation)") so a
reader can never mistake configuration for a citation.

| Rule | Logic |
| --- | --- |
| `DOCS-REQUIRED-001` | A checklist entry marked required has no document of that type. |
| `NAME-MATCH-001` | The normalized extracted organization name differs from the case record. |
| `FIELD-CONFLICT-001` | Two documents carry different normalized values for the same field. |
| `SCOPE-MATCH-001` | The prior approval states a different normalized scope item than the case. |
| `CAPACITY-MATCH-001` | Requested capacity is greater than the capacity stated in the prior approval. |
| `EXPIRY-001` | A printed validity date is earlier than the review date. |
| `DUPLICATE-001` | Two live documents in one case share a SHA-256 hash. |
| `EXTRACT-001` | Extraction failed, or configured assistance could not produce a usable result. |
| `EXTRACT-002` | Document extraction confidence is below 0.7. |

## Scope and selection

`app/services.select_rule_pack` walks published and superseded pack versions
newest-first and returns the first whose `review_cycles` (or `effective_from` /
`effective_to` range) and `request_types` cover the case. A pack that declares
only its `rules` map still applies to all cycles and request types, so old
snapshots keep working.

`rule_enabled` then applies three gates before any check runs:

1. the rule's own `review_cycles` and `request_types` must include the case;
2. if a pack matched, the pack snapshot decides the rule's enabled flag;
3. otherwise the `rule_settings` table decides, defaulting to enabled.

Any unexpected registry failure makes the rule **not** run. A check that cannot
prove it is applicable stays silent rather than guessing.

Because selection is scoped, a pack published for `scope_expansion` in `2026-27`
cannot change the outcome of a `licence_renewal` case — the scenario that made
early prototype behaviour hard to reproduce.

## Drafting and publishing

Packs are versioned rows in `rule_pack_versions`, not in-memory state:

1. `POST /api/rule-packs/drafts` stores an immutable snapshot of the enabled-rule
   map together with scope metadata and the author.
2. `POST /api/rule-packs/{version_id}/publish` requires a *different*
   administrator from the one who drafted it, then marks the version published.
3. Publishing a newer version supersedes the older one without deleting it, so
   any case decided under the earlier pack can still be explained.

Only administrators can draft or publish. Reviewers see the effective pack and
the rule registry on the **Rules & policies** page.

## Writing your own pack

1. Replace the `RULES` metadata with your own sources and clauses. Keep the rule
   IDs stable if you want historical cases to remain explainable.
2. Update `CHECKLIST` so each request type lists the documents your process
   actually requires, with a human-readable label for each.
3. Restrict `review_cycles` and `request_types` to the window you are authorized
   to cover. Do not claim a cycle you have not verified.
4. If a check needs different data, change it in `run_checks` in
   `app/services.py` — the check code and the rule metadata must stay in sync.
5. Add a case to `docs/demo_data/rule_evaluation_cases.json` with both
   `expected_rules` and `forbidden_rules`, then run
   `scripts/evaluate_rule_engine.py`. A new check without a negative case is a
   check nobody has tested for over-firing.
6. Publish through the two-administrator workflow, not by editing the database.

## Caveats

- The pack is configuration. It carries no legal weight and no approval from any
  authority.
- Findings are advisory evidence, not determinations. A person decides.
- `provisional` and `authoritative` are both `False` in the bundled pack; keep
  them honest for yours.

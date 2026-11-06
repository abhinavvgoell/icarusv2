"""Example policy pack: document types, checklists and rule metadata.

Every rule here is shipped sample configuration. None of it is a legal or
regulatory determination, and the reference-only entries exist to show the
metadata a real policy source should carry (source, clause, effective range,
approval state) without asserting any real citation.
"""
from __future__ import annotations

DEMO_REVIEW_CYCLES = ("2024-25", "2025-26", "2026-27")

DEMO_REQUEST_TYPE_LABELS = {
    "licence_renewal": "Renewal",
    "scope_expansion": "Scope expansion",
    "new_registration": "New registration",
}

RULE_PACK = {
    "id": "ICARUS-DEFAULT",
    "version": "2.0.0",
    "label": "Default example policy pack",
    "source": "Example policy pack bundled with Icarus",
    "source_url": None,
    "provisional": False,
    "authoritative": False,
    "review_cycles": list(DEMO_REVIEW_CYCLES),
    "request_types": list(DEMO_REQUEST_TYPE_LABELS),
    "approval_status": "Example configuration — not legal advice or regulatory approval",
    "notice": (
        "These are illustrative checks on sample data. Replace the pack with your own policy "
        "sources before relying on any result; every finding is advisory and needs a human decision."
    ),
}

# Document types expected per request type. Demonstration checklists only.
CHECKLIST = {
    "licence_renewal": [
        {"type": "declaration", "label": "Signed declaration", "required": True},
        {"type": "safety_certificate", "label": "Safety certificate", "required": True},
        {"type": "prior_approval", "label": "Previous approval letter", "required": True},
    ],
    "scope_expansion": [
        {"type": "declaration", "label": "Signed declaration", "required": True},
        {"type": "prior_approval", "label": "Previous approval letter", "required": True},
        {"type": "staff_declaration", "label": "Responsible staff declaration", "required": True},
    ],
    "new_registration": [
        {"type": "declaration", "label": "Signed declaration", "required": True},
        {"type": "consent_letter", "label": "Partner / landlord consent letter", "required": True},
        {"type": "facility_declaration", "label": "Facility declaration", "required": True},
    ],
}

_SAMPLE_SOURCE = "Icarus example policy pack (not a real citation)"
_DEMO_SCOPE = {
    "review_cycles": list(DEMO_REVIEW_CYCLES),
    "request_types": list(DEMO_REQUEST_TYPE_LABELS),
    "effective_from": None,
    "effective_to": None,
}

RULES = [
    {
        "id": "POLICY-REF-001", "name": "Signed submission requirement (reference example)",
        "scope": "How a policy reference entry is recorded", "status": "reference_only",
        **_DEMO_SCOPE,
        "source_quality": "Illustrative metadata shape",
        "approval_status": "Reference only — not an executable check",
        "verification_status": "Example entry — no real clause is asserted",
        "source": _SAMPLE_SOURCE, "source_version": "example-1", "clause": "Replace with your clause",
        "logic": "Reference only. Shows the metadata a real policy citation should carry: source, clause, effective range and approval state.",
        "enabled": False, "authoritative": False, "executable": False,
    },
    {
        "id": "POLICY-REF-002", "name": "Record retention requirement (reference example)",
        "scope": "How a policy reference entry is recorded", "status": "reference_only",
        **_DEMO_SCOPE,
        "source_quality": "Illustrative metadata shape",
        "approval_status": "Reference only — not an executable check",
        "verification_status": "Example entry — no real clause is asserted",
        "source": _SAMPLE_SOURCE, "source_version": "example-1", "clause": "Replace with your clause",
        "logic": "Reference only. Useful for documenting retention or record-keeping obligations the automated checks do not enforce.",
        "enabled": False, "authoritative": False, "executable": False,
    },
    {
        "id": "DOCS-REQUIRED-001", "name": "Required document completeness",
        "scope": "Request types and their document checklist", "status": "illustrative",
        **_DEMO_SCOPE,
        "source_quality": "Sample checklist shipped with the demo",
        "approval_status": "Sample check — configurable, not policy approval",
        "verification_status": "Illustrative only — not a complete real-world checklist",
        "source": _SAMPLE_SOURCE, "source_version": "Pack 2.0.0", "clause": "No clause — sample configuration",
        "logic": "Flags documents that the selected checklist marks as required but that were not uploaded.",
        "enabled": True, "authoritative": False, "executable": True,
    },
    {
        "id": "NAME-MATCH-001", "name": "Organization name consistency",
        "scope": "Uploaded documents vs application record", "status": "illustrative",
        **_DEMO_SCOPE,
        "source_quality": "Comparison logic in this repository",
        "approval_status": "Sample check — configurable, not policy approval",
        "verification_status": "Illustrative only — normalization-based comparison, not proof of error",
        "source": "Icarus extraction and normalization logic", "source_version": "Pack 2.0.0", "clause": "No clause — sample logic",
        "logic": "Compares an extracted organization name with the application record and shows the page-level evidence for review.",
        "enabled": True, "authoritative": False, "executable": True,
    },
    {
        "id": "FIELD-CONFLICT-001", "name": "Cross-document contradiction",
        "scope": "Documents inside the same application", "status": "illustrative",
        **_DEMO_SCOPE,
        "source_quality": "Comparison logic in this repository",
        "approval_status": "Sample check — configurable, not policy approval",
        "verification_status": "Illustrative only — consistency signal, not a determination",
        "source": "Icarus cross-document comparison engine", "source_version": "Pack 2.0.0", "clause": "No clause — sample logic",
        "logic": "Compares the same extracted field across documents in one application and cites both documents and pages.",
        "enabled": True, "authoritative": False, "executable": True,
    },
    {
        "id": "CAPACITY-MATCH-001", "name": "Sanctioned vs requested capacity",
        "scope": "Uploaded approval records vs requested capacity", "status": "illustrative",
        **_DEMO_SCOPE,
        "source_quality": "Comparison logic in this repository",
        "approval_status": "Sample check — configurable, not policy approval",
        "verification_status": "Illustrative only — numeric comparison, not a determination",
        "source": "Icarus capacity comparison logic", "source_version": "Pack 2.0.0", "clause": "No clause — sample logic",
        "logic": "Compares a capacity printed in a document with the capacity requested in the application.",
        "enabled": True, "authoritative": False, "executable": True,
    },
    {
        "id": "SCOPE-MATCH-001", "name": "Scope item consistency",
        "scope": "Uploaded documents vs application scope", "status": "illustrative",
        **_DEMO_SCOPE,
        "source_quality": "Comparison logic in this repository",
        "approval_status": "Sample check — configurable, not policy approval",
        "verification_status": "Illustrative only — text comparison, not a determination",
        "source": "Icarus scope comparison logic", "source_version": "Pack 2.0.0", "clause": "No clause — sample logic",
        "logic": "Compares the scope item (product, service or category applied for) in a document with the application record.",
        "enabled": True, "authoritative": False, "executable": True,
    },
    {
        "id": "EXPIRY-001", "name": "Printed validity date has passed",
        "scope": "Documents carrying an explicit valid-until date", "status": "illustrative",
        **_DEMO_SCOPE,
        "source_quality": "Date comparison in this repository",
        "approval_status": "Sample check — configurable, not policy approval",
        "verification_status": "Illustrative only — never infers a validity period",
        "source": "Icarus date comparison logic", "source_version": "Pack 2.0.0", "clause": "No clause — sample logic",
        "logic": "Flags a date printed on the document that is already past, for human confirmation.",
        "enabled": True, "authoritative": False, "executable": True,
    },
    {
        "id": "DUPLICATE-001", "name": "Exact duplicate file",
        "scope": "Uploads within one application", "status": "technical_signal",
        **_DEMO_SCOPE,
        "source_quality": "SHA-256 digest comparison",
        "approval_status": "Technical signal — not a policy rule",
        "verification_status": "Technical signal — not a policy rule",
        "source": "SHA-256 digest", "source_version": "SHA-256", "clause": "Not applicable",
        "logic": "Identifies byte-for-byte duplicate uploads as a review signal only.",
        "enabled": True, "authoritative": False, "executable": True,
    },
    {
        "id": "EXTRACT-001", "name": "Extraction needs manual review",
        "scope": "Document processing", "status": "technical_signal",
        **_DEMO_SCOPE,
        "source_quality": "Extraction pipeline in this repository",
        "approval_status": "Technical signal — not a policy rule",
        "verification_status": "Technical signal — not a policy rule",
        "source": "Icarus extraction pipeline", "source_version": "Pack 2.0.0", "clause": "Not applicable",
        "logic": "Routes unreadable or unsupported documents to a person.",
        "enabled": True, "authoritative": False, "executable": True,
    },
    {
        "id": "EXTRACT-002", "name": "Low extraction confidence",
        "scope": "Document processing", "status": "technical_signal",
        **_DEMO_SCOPE,
        "source_quality": "Extraction pipeline in this repository",
        "approval_status": "Technical signal — not a policy rule",
        "verification_status": "Technical signal — not a policy rule",
        "source": "Icarus extraction pipeline", "source_version": "Pack 2.0.0", "clause": "Not applicable",
        "logic": "Routes low-confidence extracted text to a person for confirmation.",
        "enabled": True, "authoritative": False, "executable": True,
    },
]

RULE_BY_ID = {rule["id"]: rule for rule in RULES}


def normalize(value: str | None) -> str:
    return "".join(ch.lower() for ch in (value or "") if ch.isalnum())

from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
from app.main import app
from app.storage import LocalStorage, StorageError
from app.database import _postgres_sql
from app.malware import MalwareScanUnavailable, scan

client = TestClient(app)
def headers(email, password):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"X-Session-Token": response.json()["token"]}

APPLICANT = headers("submitter@cascade.demo", "Submitter@123")
OFFICER = headers("reviewer@arbiter.demo", "Reviewer@123")

def test_health_and_seeded_findings():
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/health/ready").status_code == 200
    case = client.get("/api/applications/2", headers=APPLICANT).json()
    assert any(f["kind"] == "data_inconsistency" for f in case["findings"])
    assert any(f["title"] == "Document appears expired" for f in case["findings"])

def test_rule_registry_records_provenance_and_marks_sample_logic_non_authoritative():
    response = client.get("/api/rules", headers=OFFICER)
    assert response.status_code == 200
    payload = response.json()
    by_id = {rule["id"]: rule for rule in payload["rules"]}
    # Reference entries carry the metadata a real policy citation needs.
    assert by_id["POLICY-REF-001"]["status"] == "reference_only"
    assert by_id["POLICY-REF-001"]["clause"]
    assert by_id["POLICY-REF-001"]["source_quality"]
    assert by_id["POLICY-REF-001"]["enabled"] is False
    assert by_id["POLICY-REF-001"]["executable"] is False
    assert by_id["NAME-MATCH-001"]["status"] == "illustrative"
    assert by_id["NAME-MATCH-001"]["authoritative"] is False
    assert payload["rule_pack"]["authoritative"] is False

def test_uploaded_field_extraction_includes_source_page_reference():
    response = client.post(
        "/api/applications/2/documents?document_type=prior_approval",
        headers=APPLICANT,
        files={"file": ("evidence.txt", b"Organization Name: Cascade Chemicals Ltd\nReference No: SUB-DEMO-5", "text/plain")},
    )
    assert response.status_code == 200
    assert {item["page"] for item in response.json()["page_references"]} == {1}
    case = client.get("/api/applications/2", headers=APPLICANT).json()
    evidence = next(doc for doc in case["documents"] if doc["id"] == response.json()["document_id"])
    assert all(field["page"] == 1 for field in evidence["fields"])

def test_pdf_extraction_preserves_the_actual_page_for_mismatch_evidence():
    import pymupdf
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Cover page")
    pdf.new_page().insert_text((72, 72), "Organization Name: Different Synthetic Ltd")
    content = pdf.tobytes()
    pdf.close()
    uploaded = client.post(
        "/api/applications/2/documents?document_type=prior_approval",
        headers=APPLICANT,
        files={"file": ("evidence.pdf", content, "application/pdf")},
    )
    assert uploaded.status_code == 200
    assert {ref["page"] for ref in uploaded.json()["page_references"] if ref["field"] == "organization_name"} == {2}
    client.post("/api/applications/2/precheck", headers=OFFICER)
    case = client.get("/api/applications/2", headers=OFFICER).json()
    finding = next(item for item in case["findings"] if item["rule_id"] == "NAME-MATCH-001" and item["document_id"] == uploaded.json()["document_id"])
    assert "Source page: 2" in finding["evidence"]
    assert finding["evidence_page"] == 2
    page = client.get(f"/api/documents/{finding['document_id']}/pages/{finding['evidence_page']}", headers=OFFICER).json()
    assert page["page"] == 2 and "Different Synthetic Ltd" in page["text"]

def test_pdf_signature_field_inspection_is_scoped_and_never_claims_verification():
    import pymupdf
    created = client.post("/api/applications", headers=APPLICANT, json={
        "organization_name": "Synthetic Signature Field Test Ltd",
        "review_cycle": "2026-27",
        "request_type": "licence_renewal",
    })
    assert created.status_code == 200
    application_id = created.json()["id"]

    unsigned = pymupdf.open()
    unsigned.new_page().insert_text((72, 72), "Synthetic unsigned PDF")
    unsigned_bytes = unsigned.tobytes()
    unsigned.close()
    no_field = client.post(
        f"/api/applications/{application_id}/documents?document_type=prior_approval",
        headers=APPLICANT,
        files={"file": ("unsigned.pdf", unsigned_bytes, "application/pdf")},
    )
    assert no_field.status_code == 200
    result = client.get(f"/api/documents/{no_field.json()['document_id']}/integrity", headers=OFFICER)
    assert result.status_code == 200
    assert result.json()["status"] == "no_signature_field_detected"
    assert "Visible signatures, QR codes" in result.json()["detail"]

    signed_form = pymupdf.open()
    page = signed_form.new_page()
    widget = pymupdf.Widget()
    widget.field_name = "synthetic-signature"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
    widget.rect = pymupdf.Rect(36, 36, 220, 80)
    page.add_widget(widget)
    signature_bytes = signed_form.tobytes()
    signed_form.close()
    has_field = client.post(
        f"/api/applications/{application_id}/documents?document_type=prior_approval",
        headers=APPLICANT,
        files={"file": ("signature-field.pdf", signature_bytes, "application/pdf")},
    )
    assert has_field.status_code == 200
    inspected = client.get(f"/api/documents/{has_field.json()['document_id']}/integrity", headers=OFFICER)
    assert inspected.status_code == 200
    assert inspected.json()["status"] == "signature_field_present_unverified"
    assert inspected.json()["signature_fields"][0]["field_name"] == "synthetic-signature"
    assert "were not validated" in inspected.json()["detail"]

    other_organization = headers("submitter@northwind.demo", "Submitter@123")
    assert client.get(f"/api/documents/{has_field.json()['document_id']}/integrity", headers=other_organization).status_code == 403

    text_doc = client.post(
        f"/api/applications/{application_id}/documents?document_type=prior_approval",
        headers=APPLICANT,
        files={"file": ("plain.txt", b"Synthetic text", "text/plain")},
    )
    not_applicable = client.get(f"/api/documents/{text_doc.json()['document_id']}/integrity", headers=OFFICER)
    assert not_applicable.json()["status"] == "not_applicable"

def test_facility_image_evidence_stays_private_and_requires_visual_review():
    import pymupdf
    created = client.post("/api/applications", headers=APPLICANT, json={
        "organization_name": "Synthetic Infrastructure Review Ltd",
        "review_cycle": "2026-27",
        "request_type": "new_registration",
    })
    assert created.status_code == 200
    application_id = created.json()["id"]

    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 32, 24), 0)
    pixmap.clear_with(240)
    image_bytes = pixmap.tobytes("png")
    upload = client.post(
        f"/api/applications/{application_id}/documents?document_type=facility_declaration",
        headers=APPLICANT,
        files={"file": ("synthetic-plan.png", image_bytes, "image/png")},
    )
    assert upload.status_code == 200, upload.text
    case = client.get(f"/api/applications/{application_id}", headers=APPLICANT).json()
    doc = next(item for item in case["documents"] if item["id"] == upload.json()["document_id"])
    assert doc["extraction_error"] or doc["confidence"] < 0.7

    preview = client.get(f"/api/documents/{doc['id']}/image-preview", headers=OFFICER)
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert preview.headers["x-content-type-options"] == "nosniff"

    submitter_review = client.post(
        f"/api/applications/{application_id}/facility-reviews", headers=APPLICANT,
        json={"document_id": doc["id"], "outcome": "observed_no_issue", "note": "Submitter cannot enter reviewer review."},
    )
    assert submitter_review.status_code == 403
    blank_note = client.post(
        f"/api/applications/{application_id}/facility-reviews", headers=OFFICER,
        json={"document_id": doc["id"], "outcome": "observed_no_issue", "note": "     "},
    )
    assert blank_note.status_code == 400
    first_review = client.post(
        f"/api/applications/{application_id}/facility-reviews", headers=OFFICER,
        json={"document_id": doc["id"], "outcome": "follow_up_required", "note": "Synthetic plan image is low detail; request clearer evidence."},
    )
    assert first_review.status_code == 200 and first_review.json()["automated_assessment"] is False
    inspection = client.post(
        f"/api/applications/{application_id}/facility-reviews", headers=OFFICER,
        json={"document_id": doc["id"], "outcome": "inspection_recommended", "note": "Synthetic example: reviewer requests an on-site inspection."},
    )
    assert inspection.status_code == 200 and inspection.json()["inspection_routed"] is True
    review_list = client.get(f"/api/applications/{application_id}/facility-reviews", headers=OFFICER).json()
    assert review_list["count"] == 2 and review_list["automated_assessment"] is False
    report = client.get(f"/api/applications/{application_id}/report", headers=OFFICER).json()
    assert len(report["facility_reviews"]) == 2
    assert report["facility_reviews"][0]["document_id"] == doc["id"]
    assert any(item["type"] == "escalation" and item["application_id"] == application_id for item in client.get("/api/alerts", headers=OFFICER).json()["items"])

    other_organization = headers("submitter@northwind.demo", "Submitter@123")
    assert client.get(f"/api/documents/{doc['id']}/image-preview", headers=other_organization).status_code == 403

    mismatch = client.post(
        f"/api/applications/{application_id}/documents?document_type=facility_declaration",
        headers=APPLICANT,
        files={"file": ("fake.png", b"not a PNG", "image/png")},
    )
    assert mismatch.status_code == 400

def test_short_mp4_facility_evidence_is_signature_checked_private_and_reviewable():
    created = client.post("/api/applications", headers=APPLICANT, json={
        "organization_name": "Synthetic Video Evidence Ltd",
        "review_cycle": "2026-27",
        "request_type": "new_registration",
    })
    application_id = created.json()["id"]
    video = (16).to_bytes(4, "big") + b"ftypisom" + bytes(4)
    video += (12).to_bytes(4, "big") + b"moov" + bytes(4)
    video += (12).to_bytes(4, "big") + b"mdat" + b"data"
    upload = client.post(
        f"/api/applications/{application_id}/documents?document_type=facility_declaration",
        headers=APPLICANT,
        files={"file": ("walkthrough.mp4", video, "video/mp4")},
    )
    assert upload.status_code == 200, upload.text
    document_id = upload.json()["document_id"]
    preview = client.get(f"/api/documents/{document_id}/video-preview", headers=OFFICER)
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "video/mp4"
    assert preview.content == video
    review = client.post(
        f"/api/applications/{application_id}/facility-reviews", headers=OFFICER,
        json={"document_id": document_id, "outcome": "unable_to_assess", "note": "Synthetic clip is too short to assess the facility."},
    )
    assert review.status_code == 200 and review.json()["automated_assessment"] is False
    report = client.get(f"/api/applications/{application_id}/report", headers=OFFICER).json()
    assert report["facility_reviews"][0]["outcome"] == "unable_to_assess"
    bad = client.post(
        f"/api/applications/{application_id}/documents?document_type=facility_declaration",
        headers=APPLICANT,
        files={"file": ("fake.mp4", b"not an MP4 container", "video/mp4")},
    )
    assert bad.status_code == 400

def test_duplicate_hash_is_flagged_without_rejecting_upload():
    submitter = headers("submitter@cascade.demo", "Submitter@123")
    created = client.post("/api/applications", headers=submitter, json={
        "organization_name": "Saraswati Duplicate Signal Test",
        "review_cycle": "2026-27",
        "request_type": "licence_renewal",
    })
    application_id = created.json()["id"]
    content = b"Synthetic duplicate signal evidence\nCertificate No: DUP-101\n"
    first = client.post(f"/api/applications/{application_id}/documents?document_type=declaration", headers=submitter, files={"file": ("first.txt", content, "text/plain")})
    second = client.post(f"/api/applications/{application_id}/documents?document_type=prior_approval", headers=submitter, files={"file": ("second.txt", content, "text/plain")})
    assert first.status_code == second.status_code == 200
    assert first.json()["document_id"] != second.json()["document_id"]
    checked = client.post(f"/api/applications/{application_id}/precheck", headers=submitter)
    assert checked.status_code == 200
    case = client.get(f"/api/applications/{application_id}", headers=submitter).json()
    duplicate = next(item for item in case["findings"] if item["rule_id"] == "DUPLICATE-001")
    assert duplicate["status"] == "open"
    assert duplicate["document_id"] == second.json()["document_id"]
    assert sum(not document["superseded"] for document in case["documents"]) == 2

def test_simulated_issuer_result_has_required_disclaimer():
    result = client.post("/api/applications/2/verification/simulate", headers=OFFICER)
    assert result.status_code == 200
    assert result.json()["label"] == "Simulated — not externally verified."
    assert result.json()["requires_manual_confirmation"] is True
    case = client.get("/api/applications/2", headers=OFFICER).json()
    assert any("Simulated — not externally verified." in item["detail"] for item in case["verifications"])

def test_simulated_issuer_adapter_never_claims_external_verification_without_reference():
    from app.sources import SIMULATION_LABEL, SimulatedSourceVerifier
    result = SimulatedSourceVerifier().verify(None)
    assert result.status == "verification_pending"
    assert result.requires_manual_confirmation is True
    assert result.label == SIMULATION_LABEL
    assert SIMULATION_LABEL in result.detail
    assert "no external source was contacted" in result.detail

def test_all_six_verification_statuses_are_recordable_with_source_and_comparison():
    base = {"method": "External reference lookup", "source": "Synthetic source register", "detail": "Reviewer compared the submitted certificate reference."}
    document_id = client.get("/api/applications/2", headers=OFFICER).json()["documents"][0]["id"]
    expected = {
        "verified": ("SAFE-2002", "SAFE-2002", False),
        "mismatch": ("SAFE-2002", "SAFE-9999", False),
        "expired": ("2025-03-31", "2025-03-31", False),
        "verification_pending": (None, None, True),
        "source_unavailable": (None, None, True),
        "manual_review_required": (None, None, True),
    }
    recorded = []
    for status, (declared, returned, needs_manual) in expected.items():
        response = client.post("/api/applications/2/verification", headers=OFFICER, json={
            **base, "document_id": document_id, "status": status,
            "declared_value": declared, "source_value": returned,
        })
        assert response.status_code == 200, response.text
        assert response.json()["status"] == status
        assert response.json()["requires_manual_confirmation"] is needs_manual
        assert response.json()["label"]
        assert "Reviewer-recorded" in response.json()["label"]
        recorded.append(status)
    assert set(recorded) == {"verified", "mismatch", "expired", "verification_pending", "source_unavailable", "manual_review_required"}
    case = client.get("/api/applications/2", headers=OFFICER).json()
    statuses = {item["status"] for item in case["verifications"]}
    assert set(recorded) <= statuses
    verified_attempt = next(item for item in case["verifications"] if item["status"] == "verified")
    assert "Declared: SAFE-2002" in verified_attempt["detail"] and "Source: SAFE-2002" in verified_attempt["detail"]
    assert "Reviewer-recorded" in verified_attempt["label"] and "not machine-verified" in verified_attempt["label"]
    assert verified_attempt["requires_manual_confirmation"] is False
    assert verified_attempt["source"] == "Synthetic source register"
    pending = [item for item in case["verifications"] if item["requires_manual_confirmation"]]
    assert pending and all(item["label"] for item in pending)

def test_verification_comparison_status_requires_consistent_values():
    base = {"method": "External reference lookup", "source": "Synthetic source register", "detail": "Reviewer compared values."}
    # A comparison result cannot omit the values being compared.
    missing = client.post("/api/applications/2/verification", headers=OFFICER, json={**base, "status": "verified"})
    assert missing.status_code == 400
    # "verified" cannot be recorded when the two values differ.
    contradictory = client.post("/api/applications/2/verification", headers=OFFICER, json={**base, "status": "verified", "declared_value": "SAFE-2002", "source_value": "SAFE-9999"})
    assert contradictory.status_code == 400
    assert "mismatch" in contradictory.json()["detail"]
    # "mismatch" cannot be recorded when the two values match.
    matched = client.post("/api/applications/2/verification", headers=OFFICER, json={**base, "status": "mismatch", "declared_value": "SAFE-2002", "source_value": "SAFE-2002"})
    assert matched.status_code == 400
    assert "verified" in matched.json()["detail"]
    # Inconclusive statuses must not carry comparison values.
    stray = client.post("/api/applications/2/verification", headers=OFFICER, json={**base, "status": "source_unavailable", "declared_value": "SAFE-2002", "source_value": "SAFE-2002"})
    assert stray.status_code == 400
    # Case-insensitive comparison still counts as a match.
    relaxed = client.post("/api/applications/2/verification", headers=OFFICER, json={**base, "status": "verified", "declared_value": "SAFE-2002", "source_value": " safe-2002 "})
    assert relaxed.status_code == 200

def test_pending_and_unavailable_verifications_are_routed_to_the_reviewer_alert_queue():
    for status in ("verification_pending", "source_unavailable", "manual_review_required"):
        response = client.post("/api/applications/2/verification", headers=OFFICER, json={
            "method": "External reference lookup", "source": "Synthetic source register",
            "status": status, "detail": "Awaiting confirmation from the authorized source.",
        })
        assert response.status_code == 200
    alerts = client.get("/api/alerts", headers=OFFICER).json()
    routed = [item for item in alerts["items"] if item["type"] == "verification"]
    case_two = [item for item in routed if item["application_id"] == 2]
    # This case alone now has the three unresolved results queued for a person.
    assert {item["status"] for item in case_two} >= {"verification_pending", "source_unavailable", "manual_review_required"}
    # Seeded cases with unresolved verification also appear, so no case is silently dropped.
    assert len(routed) >= len(case_two) and routed
    assert all(item["title"] and item["explanation"] for item in routed)
    # A resolved comparison result does not create an unresolved alert.
    client.post("/api/applications/2/verification", headers=OFFICER, json={
        "method": "External reference lookup", "source": "Synthetic source register",
        "status": "verified", "declared_value": "SAFE-2002", "source_value": "SAFE-2002",
        "detail": "Confirmed against the authorized source.",
    })
    after = client.get("/api/alerts", headers=OFFICER).json()
    assert not any(item["status"] == "verified" for item in after["items"] if item["type"] == "verification")

def test_application_list_returns_rows_for_reviewer_and_submitter():
    # Regression: this endpoint previously raised NameError (HTTP 500) because
    # select_rule_pack was not imported, and no test exercised its body.
    reviewer_list = client.get("/api/applications", headers=OFFICER)
    assert reviewer_list.status_code == 200, reviewer_list.text
    rows = reviewer_list.json()
    assert len(rows) >= 2
    sample = next(item for item in rows if item["id"] == 2)
    assert sample["open_findings_count"] >= 0
    assert sample["document_count"] >= 1
    assert sample["request_type_label"]
    assert sample["rule_pack_version"]
    assert "assigned_to" in sample and "escalated_at" in sample
    assert isinstance(sample["checks_run"], bool)
    # Submitter sees only its own organization and never another's case.
    submitter_list = client.get("/api/applications", headers=APPLICANT)
    assert submitter_list.status_code == 200
    assert submitter_list.json() and all(item["organization_id"] == "ORG-1002" for item in submitter_list.json())
    northwind = headers("submitter@northwind.demo", "Submitter@123")
    assert all(item["organization_id"] == "ORG-1001" for item in client.get("/api/applications", headers=northwind).json())

def test_reviewer_assignment_records_ownership_and_rejects_non_reviewers():
    roster = client.get("/api/reviewers", headers=OFFICER)
    assert roster.status_code == 200
    assert any(item["email"] == "reviewer@arbiter.demo" for item in roster.json()["reviewers"])
    assert client.get("/api/reviewers", headers=APPLICANT).status_code == 403
    assigned = client.post("/api/applications/2/assign", headers=OFFICER, json={"reviewer_email": "reviewer@arbiter.demo"})
    assert assigned.status_code == 200
    assert assigned.json()["assigned_to"] == "reviewer@arbiter.demo"
    case = client.get("/api/applications/2", headers=OFFICER).json()
    assert case["assigned_to"] == "reviewer@arbiter.demo"
    assert any(event["action"] == "case_assigned" for event in case["audit"])
    # Only active reviewer accounts can own a case.
    rejected = client.post("/api/applications/2/assign", headers=OFFICER, json={"reviewer_email": "submitter@cascade.demo"})
    assert rejected.status_code == 400
    cleared = client.post("/api/applications/2/assign", headers=OFFICER, json={"reviewer_email": None})
    assert cleared.status_code == 200 and cleared.json()["assigned_to"] is None
    assert client.post("/api/applications/2/assign", headers=APPLICANT, json={"reviewer_email": "reviewer@arbiter.demo"}).status_code == 403

def test_reviewer_assignment_enforces_case_scope_across_lists_alerts_and_direct_routes():
    import app.main as main

    conn = main.db()
    conn.execute("INSERT INTO users(email,role,organization_id,password_hash,active) VALUES(?,?,?,?,TRUE)",
                 ("reviewer.second@aicte.demo", "reviewer", None, main.hash_password("SecondReviewer@123")))
    conn.commit(); conn.close()
    second = headers("reviewer.second@aicte.demo", "SecondReviewer@123")
    admin = headers("admin@arbiter.demo", "Admin@123")
    created = client.post("/api/applications", headers=APPLICANT, json={
        "organization_name": "Saraswati Reviewer Scope Test", "review_cycle": "2026-27",
        "request_type": "licence_renewal",
    })
    assert created.status_code == 200
    application_id = created.json()["id"]

    # Unassigned work appears in both reviewers' shared capacity queues.
    assert any(item["id"] == application_id for item in client.get("/api/applications", headers=second).json())
    assert client.post(f"/api/applications/{application_id}/assign", headers=OFFICER,
                       json={"reviewer_email": "reviewer.second@aicte.demo"}).status_code == 403
    claimed = client.post(f"/api/applications/{application_id}/assign", headers=OFFICER,
                          json={"reviewer_email": "reviewer@arbiter.demo"})
    assert claimed.status_code == 200

    client.post(f"/api/applications/{application_id}/precheck", headers=OFFICER)
    finding_id = next(item["id"] for item in client.get(f"/api/applications/{application_id}", headers=OFFICER).json()["findings"] if item["status"] == "open")
    assert all(item["id"] != application_id for item in client.get("/api/applications", headers=second).json())
    assert all(item["id"] != application_id for item in client.get("/api/dashboard", headers=second).json()["recent"])
    assert all(item["application_id"] != application_id for item in client.get("/api/alerts", headers=second).json()["items"])
    assert client.get(f"/api/applications/{application_id}", headers=second).status_code == 404
    assert client.get(f"/api/applications/{application_id}/report", headers=second).status_code == 404
    assert client.post(f"/api/findings/{finding_id}/review", headers=second,
                       json={"outcome": "accepted", "reason": "Not assigned to this reviewer."}).status_code == 404

    # Administrators can route the case to another active reviewer; the former
    # assignee immediately loses access and the new assignee gains it.
    reassigned = client.post(f"/api/applications/{application_id}/assign", headers=admin,
                             json={"reviewer_email": "reviewer.second@aicte.demo"})
    assert reassigned.status_code == 200
    assert client.get(f"/api/applications/{application_id}", headers=OFFICER).status_code == 404
    assert client.get(f"/api/applications/{application_id}", headers=second).status_code == 200
    assert client.get(f"/api/applications/{application_id}", headers=admin).status_code == 200

def test_escalation_routes_case_for_official_intervention_and_can_be_resolved():
    created = client.post("/api/applications", headers=APPLICANT, json={
        "organization_name": "Saraswati Escalation Workflow Test",
        "review_cycle": "2026-27",
        "request_type": "licence_renewal",
    })
    application_id = created.json()["id"]
    escalated = client.post(f"/api/applications/{application_id}/escalate", headers=OFFICER, json={
        "level": "inspection_required",
        "reason": "Infrastructure evidence is incomplete and requires a site visit.",
    })
    assert escalated.status_code == 200
    assert escalated.json()["escalation_level"] == "inspection_required"
    assert escalated.json()["routed_to_alerts"] is True
    case = client.get(f"/api/applications/{application_id}", headers=OFFICER).json()
    assert case["escalated_at"] and case["escalated_by"] == "reviewer@arbiter.demo"
    assert "site visit" in case["escalation_reason"]
    assert any(event["action"] == "escalated" for event in case["audit"])
    alerts = client.get("/api/alerts", headers=OFFICER).json()
    queued = [item for item in alerts["items"] if item["type"] == "escalation" and item["application_id"] == application_id]
    assert len(queued) == 1
    assert queued[0]["severity"] == "critical"
    assert "site visit" in queued[0]["explanation"]
    assert client.post(f"/api/applications/{application_id}/assign", headers=OFFICER, json={"reviewer_email": "reviewer@arbiter.demo"}).status_code == 200
    dashboard = client.get("/api/dashboard", headers=OFFICER).json()
    assert dashboard["escalated_applications"] >= 1 and dashboard["assigned_applications"] >= 1
    resolved = client.post(f"/api/applications/{application_id}/escalation/resolve", headers=OFFICER)
    assert resolved.status_code == 200
    after = client.get(f"/api/applications/{application_id}", headers=OFFICER).json()
    assert after["escalated_at"] is None and after["escalation_level"] is None
    assert any(event["action"] == "escalation_resolved" for event in after["audit"])
    assert not any(item["type"] == "escalation" and item["application_id"] == application_id for item in client.get("/api/alerts", headers=OFFICER).json()["items"])
    # Resolving a case with no open escalation is a conflict, not a silent no-op.
    assert client.post(f"/api/applications/{application_id}/escalation/resolve", headers=OFFICER).status_code == 409
    assert client.post(f"/api/applications/{application_id}/escalate", headers=APPLICANT, json={"level": "senior_review", "reason": "Submitter must not escalate a case."}).status_code == 403
    assert client.post(f"/api/applications/{application_id}/escalate", headers=OFFICER, json={"level": "not_a_level", "reason": "Unsupported escalation level."}).status_code == 422

def test_verification_requires_reviewer_role():
    submitter_attempt = client.post("/api/applications/2/verification", headers=APPLICANT, json={
        "method": "External reference lookup", "source": "Synthetic source register",
        "status": "verified", "declared_value": "SAFE-2002", "source_value": "SAFE-2002",
        "detail": "Submitter must not record reviewer verification results.",
    })
    assert submitter_attempt.status_code == 403

def test_local_ai_status_is_explicitly_on_device_and_advisory():
    result = client.get("/api/ai/status", headers=APPLICANT)
    assert result.status_code == 200
    assert result.json()["external_processing"] is False
    assert result.json()["classifier_model"].startswith("sentence-transformers/")
    assert result.json()["extractor_model"].startswith("Xenova/")

def test_optional_llm_adapter_requires_exact_source_quote_and_page():
    import json
    from app.llm import OpenAIResponsesExtractor

    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"status": "completed", "output": [{"type": "message", "content": [{
                "type": "output_text", "text": json.dumps({"fields": [
                    {"field_name": "organization_name", "value": "Saraswati Ltd", "page": 2,
                     "quote": "Organization Name: Saraswati Ltd"},
                    {"field_name": "certificate_number", "value": "HALLUCINATED-8", "page": 2,
                     "quote": "Organization Name: Saraswati Ltd"},
                    {"field_name": "valid_until", "value": "2030-01-01", "page": 99,
                     "quote": "Valid until: 2030-01-01"},
                ]})
            }]}]}

    class Client:
        payload = None
        def post(self, url, **kwargs):
            self.payload = kwargs["json"]
            return Response()

    fake_client = Client()
    extractor = OpenAIResponsesExtractor("synthetic-test-key", "synthetic-test-model", fake_client)
    fields = extractor.extract_fields(["Cover page", "Organization Name: Saraswati Ltd"])
    assert [(field["field_name"], field["page"], field["source"]) for field in fields] == [
        ("organization_name", 2, "llm_structured")
    ]
    assert fake_client.payload["store"] is False
    assert fake_client.payload["text"]["format"]["strict"] is True
    assert fake_client.payload["text"]["format"]["schema"]["additionalProperties"] is False

def test_openai_provider_is_opt_in_and_discloses_external_processing(monkeypatch):
    from app import ai

    monkeypatch.setenv("APP_AI_PROVIDER", "openai")
    monkeypatch.setenv("APP_AI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    status = ai.status()
    assert status["provider"] == "openai"
    assert status["external_processing"] is True
    assert "sent externally" in status["label"]

def test_benchmark_scoring_requires_exact_value_and_page():
    from scripts.evaluate_ai_extraction import score_cases
    cases = [{"id": "synthetic-1", "document_type": "safety_certificate", "expected_fields": [
        {"field_name": "organization_name", "value": "Aster Institute", "page": 2},
        {"field_name": "certificate_number", "value": "F-77", "page": 2},
    ]}]
    result = score_cases(cases, {"synthetic-1": [
        {"field_name": "organization_name", "raw_value": "Aster Institute", "page": 1},
        {"field_name": "certificate_number", "raw_value": "F-77", "page": 2},
    ]})
    assert result["exact_value_and_page"] == 1
    assert result["value_correct_page_wrong"] == 1
    assert result["recall"] == 0.5

def test_deterministic_extraction_covers_common_source_label_variants():
    from app.services import extract_fields
    assert extract_fields("Name of Organization - Cobalt River Institute\nSerial: FS/77/26\nExpiry Date: 2031-03-01") == {
        "organization_name": "Cobalt River Institute", "valid_until": "2031-03-01", "certificate_number": "FS/77/26",
    }
    assert extract_fields("Certificate Number\nHFS-6002") == {"certificate_number": "HFS-6002"}
    assert extract_fields("Approval valid through 2028-07-31") == {"valid_until": "2028-07-31"}
    assert "certificate_number" not in extract_fields("Certificate granted to Aster Valley Holdings")

def test_upload_persists_ai_suggestion_and_field_provenance(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.ai, "analyze", lambda pages: {
        "status": "complete", "suggested_type": "safety_certificate", "similarity": 0.71,
        "fields": [{"field_name": "certificate_number", "raw_value": "FS-42",
                    "normalized_value": "FS-42", "page": 1, "confidence": 0.88,
                    "source": "local_ai_qa"}],
        "detail": "AI suggestions only; verify against the document.", "model": "synthetic-test-model",
    })
    uploaded = client.post(
        "/api/applications/2/documents?document_type=safety_certificate",
        headers=APPLICANT,
        files={"file": ("fire.txt", b"This safety record concerns Saraswati Ltd.", "text/plain")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["ai_analysis"]["suggested_type"] == "safety_certificate"
    case = client.get("/api/applications/2", headers=APPLICANT).json()
    document = next(item for item in case["documents"] if item["id"] == uploaded.json()["document_id"])
    assert document["ai_analysis"]["model"] == "synthetic-test-model"
    assert next(field for field in document["fields"] if field["field_name"] == "certificate_number")["source"] == "local_ai_qa"

def test_ai_unavailable_upload_is_routed_to_manual_review(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.ai, "analyze", lambda pages: {
        "status": "manual_review", "suggested_type": None, "similarity": None,
        "fields": [], "detail": "Local AI unavailable; manual review required.", "model": None,
    })
    uploaded = client.post(
        "/api/applications/2/documents?document_type=safety_certificate",
        headers=APPLICANT,
        files={"file": ("manual-review.txt", b"Synthetic record with no structured fields.", "text/plain")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["ai_analysis"]["status"] == "manual_review"
    assert client.post("/api/applications/2/precheck", headers=OFFICER).status_code == 200
    case = client.get("/api/applications/2", headers=OFFICER).json()
    # A deployment without the optional assist install records the status but does not
    # raise a document finding: the submission itself is not defective.
    assert not any(f["rule_id"] == "EXTRACT-001" and f["document_id"] == uploaded.json()["document_id"] for f in case["findings"])

def test_failed_assistance_raises_extraction_review_finding(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.ai, "analyze", lambda pages: {
        "status": "manual_review", "suggested_type": None, "similarity": None,
        "fields": [], "detail": "Local AI unavailable; manual review required.", "model": None,
    })
    monkeypatch.setattr(main.ai, "assistance_attempted", lambda: True)
    uploaded = client.post(
        "/api/applications/2/documents?document_type=safety_certificate",
        headers=APPLICANT,
        files={"file": ("assist-failed.txt", b"Synthetic record with no structured fields.", "text/plain")},
    )
    assert uploaded.status_code == 200
    assert client.post("/api/applications/2/precheck", headers=OFFICER).status_code == 200
    case = client.get("/api/applications/2", headers=OFFICER).json()
    assert any(f["title"] == "AI assistance needs manual review" and f["document_id"] == uploaded.json()["document_id"] for f in case["findings"])

def test_ai_disabled_returns_manual_review_without_inference(monkeypatch):
    from app import ai

    monkeypatch.setenv("APP_AI_ENABLED", "false")
    result = ai.analyze(["synthetic document text"])
    assert result["status"] == "manual_review"
    assert result["fields"] == []
    assert "disabled" in result["detail"]

def test_local_transformers_classify_and_extract_synthetic_document():
    from app import ai
    if not ai.model_assets_present():
        pytest.skip("Download local AI models to run the on-device inference acceptance test")
    result = ai.analyze(["Cover page with no application details.", "Fire safety certificate issued by the State Fire Department to Saraswati Freight Services. Certificate serial F-881-42. The certificate is valid until 2030-12-31."])
    assert result["status"] == "complete"
    assert result["suggested_type"] == "safety_certificate"
    values = {field["field_name"]: field for field in result["fields"]}
    assert values["organization_name"]["raw_value"] == "Saraswati Freight Services"
    assert values["certificate_number"]["raw_value"] == "F-881-42"
    assert values["valid_until"]["raw_value"] == "2030-12-31"
    assert {field["page"] for field in result["fields"]} == {2}
    assert all(field["source"] == "local_ai_qa" for field in result["fields"])
    assert values["certificate_number"]["raw_value"] != "Cover page"

def test_local_transformer_classifier_handles_demo_document_types():
    from app import ai
    if not ai.model_assets_present():
        pytest.skip("Download local AI models to run the on-device classification acceptance test")
    examples = {
        "declaration": "Affidavit sworn before a notary by the chairman of the organization declaring the submitted particulars true.",
        "safety_certificate": "Fire safety certificate issued by the State Fire Department certifying fire protection and emergency exits.",
        "prior_approval": "Prior approval record stating the approved scope item and the sanctioned capacity.",
        "staff_declaration": "Statement of faculty members, professors, lecturers and teaching staff employed at the organization.",
        "consent_letter": "Consent letter permitting the operator to handle the requested scope item.",
        "facility_declaration": "Organization building and facility declaration describing classrooms, laboratories, floor plan and facilities.",
    }
    model = ai.get_engine()
    for expected_type, text in examples.items():
        suggested_type, similarity = model.classify([text])
        assert suggested_type == expected_type, f"expected {expected_type}, got {suggested_type} ({similarity:.3f})"

def test_upload_executes_and_persists_local_ai_when_models_are_installed():
    from app import ai
    from pathlib import Path
    if not ai.model_assets_present():
        pytest.skip("Download local AI models to run the upload inference integration test")
    response = client.post(
        "/api/applications/2/documents?document_type=safety_certificate",
        headers=APPLICANT,
        files={"file": ("ai_safety_certificate_mismatch.txt", (Path(__file__).parent.parent / "docs" / "demo_data" / "ai_safety_certificate_mismatch.txt").read_bytes(), "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["ai_analysis"]["suggested_type"] == "safety_certificate"
    case = client.get("/api/applications/2", headers=APPLICANT).json()
    document = next(item for item in case["documents"] if item["id"] == response.json()["document_id"])
    assert document["ai_analysis"]["status"] == "complete"
    fields = {field["field_name"]: field for field in document["fields"]}
    assert set(fields) >= {"organization_name", "certificate_number", "valid_until"}
    assert fields["organization_name"]["source"] == "local_ai_qa"
    assert fields["certificate_number"]["source"] == "pattern"
    assert all(field["page"] == 1 for field in fields.values())
    assert client.post("/api/applications/2/precheck", headers=OFFICER).status_code == 200
    reviewed_case = client.get("/api/applications/2", headers=OFFICER).json()
    mismatch = next(f for f in reviewed_case["findings"] if f["rule_id"] == "NAME-MATCH-001" and f["document_id"] == document["id"])
    assert "Northwind Logistics Ltd" in mismatch["evidence"]
    assert "Source page: 1" in mismatch["evidence"]

def test_login_throttle_is_shared_database_state_and_limits_attempts(monkeypatch):
    import hashlib
    import uuid
    import app.main as main

    monkeypatch.setattr(main, "LOGIN_ATTEMPTS_PER_WINDOW", 2)
    email = f"unknown-{uuid.uuid4().hex}@example.test"
    assert client.post("/api/auth/login", json={"email": email, "password": "incorrect-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": "incorrect-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": "incorrect-password"}).status_code == 429

    key = hashlib.sha256(email.encode("utf-8")).hexdigest()
    first = main.db()
    row = first.execute("SELECT account_key, attempts FROM login_attempts WHERE account_key=?", (key,)).fetchone()
    first.close()
    second = main.db()
    row_again = second.execute("SELECT attempts FROM login_attempts WHERE account_key=?", (key,)).fetchone()
    second.execute("DELETE FROM login_attempts WHERE account_key=?", (key,))
    second.commit(); second.close()
    assert row["account_key"] == key and row["account_key"] != email
    assert row["attempts"] == row_again["attempts"] == 3

def test_reviewer_rule_metadata_is_attached_to_findings():
    client.post("/api/applications/2/precheck", headers=OFFICER)
    findings = client.get("/api/applications/2", headers=OFFICER).json()["findings"]
    finding = next(item for item in findings if item["rule_id"] == "NAME-MATCH-001" and item["status"] == "open")
    assert finding["source_version"] == "Pack 1.0.0"
    assert finding["authoritative"] is False
    assert "Source page: 1" in finding["evidence"]

def test_readiness_fails_when_configured_scanner_is_unavailable(monkeypatch):
    import app.main as main

    def unavailable():
        raise MalwareScanUnavailable("test scanner unavailable")

    monkeypatch.setattr(main, "check_malware_scanner", unavailable)
    response = client.get("/api/health/ready")
    assert response.status_code == 503

def test_readiness_closes_database_connection_when_query_fails(monkeypatch):
    import app.main as main

    class BrokenConnection:
        closed = False
        def execute(self, *_args): raise RuntimeError("database unavailable")
        def close(self): self.closed = True

    connection = BrokenConnection()
    monkeypatch.setattr(main, "db", lambda: connection)
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert connection.closed

def test_upload_reports_storage_write_conflict_as_unavailable(monkeypatch):
    import app.main as main

    class FailingStorage:
        def put(self, *_args): raise StorageError("create-only object conflict")

    monkeypatch.setattr(main, "storage", FailingStorage())
    response = client.post(
        "/api/applications/2/documents?document_type=declaration",
        headers=APPLICANT,
        files={"file": ("evidence.txt", b"Organization Name: Greenfield Trading", "text/plain")},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Document storage is unavailable"

def test_submitter_cannot_read_another_organization():
    response = client.get("/api/applications/2", headers=headers("submitter@northwind.demo", "Submitter@123"))
    assert response.status_code == 403

def test_reviewer_can_review_and_audit():
    assert client.post("/api/applications/2/precheck", headers=OFFICER).status_code == 200
    finding = next(f for f in client.get("/api/applications/2", headers=OFFICER).json()["findings"] if f["status"] == "open")
    response = client.post(f"/api/findings/{finding['id']}/review", headers=OFFICER, json={"outcome":"accepted","reason":"Evidence reviewed in prototype test."})
    assert response.status_code == 200
    reviewed = client.get("/api/applications/2", headers=OFFICER).json()
    saved = next(item for item in reviewed["findings"] if item["id"] == finding["id"])
    assert saved["status"] == "accepted"
    assert saved["decision_reason"] == "Evidence reviewed in prototype test."
    assert any(x["action"] == "finding_accepted" for x in reviewed["audit"])

def test_demo_findings_do_not_block_submitter_submission_to_reviewer():
    created = client.post("/api/applications", headers=APPLICANT, json={
        "organization_name": "Saraswati Submission Workflow Test",
        "review_cycle": "2026-27",
        "request_type": "licence_renewal",
    })
    assert created.status_code == 200
    response = client.post(f"/api/applications/{created.json()['id']}/submit", headers=APPLICANT)
    assert response.status_code == 200
    assert response.json()["status"] == "submitted"
    assert response.json()["finding_count"] >= 1

def test_scoped_rule_pack_draft_requires_independent_approval_and_applies_immutable_snapshot():
    admin_a = headers("admin@arbiter.demo", "Admin@123")
    draft = client.post("/api/rule-packs/drafts", headers=admin_a, json={
        "changes": [{"rule_id": "NAME-MATCH-001", "enabled": False}],
        "reason": "Disable this illustrative check for the selected demo year.",
        "effective_from": "2026-27", "effective_to": "2026-27",
        "review_cycles": ["2026-27"], "request_types": ["new_registration"],
        "source_quality": "synthetic", "source_reference": "Test-only illustrative rule override",
    })
    assert draft.status_code == 200
    version_id = draft.json()["id"]
    assert client.post(f"/api/rule-packs/{version_id}/publish", headers=admin_a).status_code == 403
    admin_b = headers("reviewer@arbiter.demo", "Reviewer@123")
    assert client.post(f"/api/rule-packs/{version_id}/publish", headers=admin_b).status_code == 403
    # Independent approval is restricted to an admin; provision a second local admin for the test.
    admin_two_email = "second-admin@test.demo"
    admin_two_password = "Admin@456789"
    existing_admins = client.get("/api/admin/users", headers=admin_a)
    if any(item["email"] == admin_two_email for item in existing_admins.json()):
        admin_two_auth = headers(admin_two_email, admin_two_password)
    else:
        admin_two = client.post("/api/admin/users", headers=admin_a, json={"email":admin_two_email,"role":"admin","initial_password":admin_two_password})
        assert admin_two.status_code == 200
        admin_two_auth = headers(admin_two_email, admin_two_password)
    published = client.post(f"/api/rule-packs/{version_id}/publish", headers=admin_two_auth)
    assert published.status_code == 200
    # The scoped new-registration pack affects its own request_type only, leaving seeded renewal cases on the previous version.
    assert client.post("/api/applications/2/precheck", headers=OFFICER).status_code == 200
    eoa_findings = client.get("/api/applications/2", headers=OFFICER).json()["findings"]
    assert any(finding["rule_id"] == "NAME-MATCH-001" and finding["status"] == "open" for finding in eoa_findings)
    assert all(finding["rule_pack_version"] != published.json()["version"] for finding in eoa_findings)
    eoa_case = client.get("/api/applications/2", headers=OFFICER).json()
    assert eoa_case["rule_pack"]["version"] != published.json()["version"]
    assert eoa_case["rule_execution"]["NAME-MATCH-001"] is True
    submitter_northwind = headers("submitter@northwind.demo", "Submitter@123")
    created = client.post("/api/applications", headers=submitter_northwind, json={"organization_name":"Greenfield Synthetic New Site Ltd","review_cycle":"2026-27","request_type":"new_registration","scope_name":"Bulk Storage","requested_capacity":60})
    case_b = client.get(f"/api/applications/{created.json()['id']}", headers=submitter_northwind).json()
    assert case_b["request_type"] == "new_registration"
    assert client.post(f"/api/applications/{created.json()['id']}/precheck", headers=submitter_northwind).status_code == 200
    applied = client.get(f"/api/applications/{created.json()['id']}", headers=submitter_northwind).json()
    assert applied["findings"]
    assert all(finding["rule_pack_version"] == published.json()["version"] for finding in applied["findings"])
    assert applied["rule_pack"]["version"] == published.json()["version"]
    assert applied["rule_execution"]["NAME-MATCH-001"] is False
    history = client.get("/api/rules", headers=admin_two_auth).json()["versions"]
    saved_version = next(version for version in history if version["id"] == version_id)
    assert saved_version["scope"]["request_types"] == ["new_registration"]
    assert saved_version["scope"]["review_cycles"] == ["2026-27"]
    assert saved_version["scope"]["source_quality"] == "synthetic"
    assert saved_version["scope"]["source_reference"] == "Test-only illustrative rule override"

def test_dashboard_alerts_and_report_have_real_data_and_case_scope():
    assert client.post("/api/applications/2/precheck", headers=OFFICER).status_code == 200
    alerts = client.get("/api/alerts", headers=OFFICER)
    assert alerts.status_code == 200 and alerts.json()["count"] > 0
    assert any(item["application_id"] == 2 and item["type"] == "finding" for item in alerts.json()["items"])
    dashboard = client.get("/api/dashboard", headers=OFFICER).json()
    assert dashboard["applications"] >= 2 and dashboard["open_findings"] > 0
    assert sum(dashboard["application_status_breakdown"].values()) == dashboard["applications"]
    assert sum(dashboard["request_type_breakdown"].values()) == dashboard["applications"]
    assert dashboard["open_alerts"] >= dashboard["open_findings"]
    assert sum(dashboard["alert_type_breakdown"].values()) == dashboard["open_alerts"]
    report = client.get("/api/applications/2/report", headers=OFFICER)
    assert report.status_code == 200
    assert report.json()["case"]["reference"]
    assert report.json()["case"]["checks_run"] is True
    assert report.json()["rule_pack"]["version"] is not None
    assert report.json()["rule_pack"]["version"] == report.json()["case"]["rule_pack"]["version"]
    assert "advisory" in report.json()["disclaimer"].lower()

def test_rectification_gate_and_replacement_are_audited():
    clarification = client.post("/api/applications/2/clarifications", headers=OFFICER, json={"message":"Please provide a corrected fire certificate."})
    assert clarification.status_code == 200
    case = client.get("/api/applications/2", headers=APPLICANT).json()
    original = next(doc for doc in case["documents"] if doc["document_type"] == "safety_certificate" and not doc["superseded"])
    blocked = client.post("/api/applications/2/documents?document_type=safety_certificate&replaces_document_id=99999", headers=APPLICANT, files={"file":("replacement.txt", b"Organization Name: Cascade Chemicals Ltd", "text/plain")})
    assert blocked.status_code == 400
    replacement = client.post(f"/api/applications/2/documents?document_type=safety_certificate&replaces_document_id={original['id']}", headers=APPLICANT, files={"file":("replacement.txt", b"Organization Name: Cascade Chemicals Ltd\\nValid Until: 2030-12-31", "text/plain")})
    assert replacement.status_code == 200
    case = client.get("/api/applications/2", headers=APPLICANT).json()
    assert any(doc["id"] == original["id"] and doc["superseded"] for doc in case["documents"])
    assert any(event["action"] == "document_replaced" for event in case["audit"])

def test_unauthenticated_demo_role_header_is_rejected():
    response = client.get("/api/applications", headers={"X-Demo-User": "reviewer@arbiter.demo"})
    assert response.status_code == 401

def test_browser_security_headers_are_present():
    response = client.get("/", headers={"X-Request-ID": "test-correlation-123"})
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"] == "test-correlation-123"

def test_sessions_have_an_expiry():
    token = headers("submitter@northwind.demo", "Submitter@123")["X-Session-Token"]
    import app.main as main
    conn = main.db()
    row = conn.execute("SELECT token, expires_at FROM sessions WHERE token=?", (main.session_token_hash(token),)).fetchone()
    conn.close()
    assert row["expires_at"]
    assert row["token"] != token
    assert len(row["token"]) == 64

def test_audit_hash_canonicalizes_sqlite_and_postgres_timestamps():
    from datetime import datetime
    from app.main import audit_event_hash

    timestamp = "2026-10-27T18:32:37.841361+00:00"
    postgres_timestamp = datetime.fromisoformat(timestamp)
    assert audit_event_hash("prior", 7, "reviewer@example.edu", "reviewed", "detail", timestamp) == audit_event_hash("prior", 7, "reviewer@example.edu", "reviewed", "detail", postgres_timestamp)

def test_postgres_audit_append_locks_application_before_reading_previous_hash():
    from app.database import PostgresConnection
    from app.main import audit

    class FakeConnection:
        def __init__(self): self.statements = []
        def execute(self, sql, params=()):
            self.statements.append(sql)
            class Result:
                def fetchone(self): return None
            return Result()

    underlying = FakeConnection()
    audit(PostgresConnection(underlying), 9, {"email": "reviewer@example.edu"}, "reviewed", "Evidence reviewed")
    assert underlying.statements[0] == "SELECT id FROM applications WHERE id=%s FOR UPDATE"
    assert "SELECT event_hash FROM audit_events" in underlying.statements[1]
    assert "INSERT INTO audit_events" in underlying.statements[2]

def test_local_storage_keeps_documents_private_to_the_application(tmp_path):
    storage = LocalStorage(tmp_path / "private")
    storage.initialize()
    storage.put("documents/example.txt", b"confidential")
    storage.put("documents/example.txt", b"confidential")
    assert storage.exists("documents/example.txt")
    assert not storage.exists("documents/missing.txt")
    assert storage.get("documents/example.txt") == b"confidential"
    with pytest.raises(StorageError, match="Refusing to replace"):
        storage.put("documents/example.txt", b"different bytes")

def test_s3_storage_uses_kms_and_closes_download_streams(monkeypatch):
    import boto3
    from app.storage import S3Storage

    class FakeS3:
        def __init__(self): self.put_options = None; self.closed = False; self.objects = {}
        def head_bucket(self, **kwargs): self.bucket = kwargs["Bucket"]
        def head_object(self, **kwargs):
            if kwargs["Key"] not in self.objects:
                from botocore.exceptions import ClientError
                raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
            return {"ContentLength": len(self.objects[kwargs["Key"]])}
        def put_object(self, **kwargs):
            self.put_options = kwargs
            if kwargs["Key"] in self.objects:
                from botocore.exceptions import ClientError
                raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
            self.objects[kwargs["Key"]] = kwargs["Body"]
        def get_object(self, **kwargs):
            owner = self
            class Body:
                def read(self): return owner.objects[kwargs["Key"]]
                def close(self): owner.closed = True
            return {"Body": Body()}

    fake = FakeS3()
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: fake)
    storage = S3Storage("private-bucket", "arbiter", "ap-south-1", None, "AWS:KMS", "key-id")
    storage.initialize()
    storage.put("document.bin", b"data")
    assert storage.exists("document.bin")
    assert fake.put_options["Key"] == "arbiter/document.bin"
    assert fake.put_options["ServerSideEncryption"] == "aws:kms"
    assert fake.put_options["SSEKMSKeyId"] == "key-id"
    assert fake.put_options["IfNoneMatch"] == "*"
    storage.put("document.bin", b"data")
    with pytest.raises(StorageError, match="Refusing to replace"):
        storage.put("document.bin", b"changed")
    assert storage.get("document.bin") == b"data"
    assert fake.closed

def test_pdf_page_limit_routes_large_documents_to_manual_review(monkeypatch):
    import pymupdf
    import app.services as services

    document = pymupdf.open()
    document.new_page()
    document.new_page()
    content = document.tobytes()
    document.close()
    monkeypatch.setattr(services, "MAX_PDF_PAGES", 1)
    text, confidence, error = services.extract_text("large.pdf", content)
    assert text == ""
    assert confidence == 0.0
    assert "1-page processing limit" in error

def test_scanned_pdf_ocr_preserves_page_evidence_and_marks_manual_review(monkeypatch):
    import pymupdf
    import app.services as services
    source = pymupdf.open()
    source_page = source.new_page(width=600, height=240)
    source_page.insert_text((30, 90), "Organization Name: OCR Demo Ltd", fontsize=28)
    raster = source_page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png")
    source.close()
    pdf = pymupdf.open()
    first = pdf.new_page()
    first.insert_text((72, 72), "Embedded page one")
    second = pdf.new_page(width=600, height=240)
    second.insert_image(second.rect, stream=raster)
    content = pdf.tobytes()
    pdf.close()

    monkeypatch.setattr(services.shutil, "which", lambda name: "tesseract.exe")
    monkeypatch.setattr(services.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "Organization Name: OCR Demo Ltd", "stderr": ""})())
    pages, confidence, error = services.extract_page_texts("mixed.pdf", content)
    assert pages[0].strip() == "Embedded page one"
    assert "OCR Demo Ltd" in pages[1]
    assert confidence == 0.65
    assert "human confirmation" in error
    assert services.extract_fields_with_pages(pages) == [("organization_name", "OCR Demo Ltd", 2)]

def test_postgres_adapter_translates_parameters_and_idempotent_insert():
    assert _postgres_sql("SELECT * FROM users WHERE email=?") == "SELECT * FROM users WHERE email=%s"
    assert _postgres_sql("INSERT OR IGNORE INTO users(email) VALUES(?)") == "INSERT INTO users(email) VALUES(%s) ON CONFLICT DO NOTHING"

def test_sqlite_migration_rejects_demo_identities_and_unsafe_or_changed_documents(tmp_path):
    import sqlite3
    from app.services import sha256
    from scripts.migrate_sqlite_to_postgres import (
        MigrationError, _assert_source_ready, _verify_document_files,
    )

    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    for table in (
        "users", "applications", "sessions", "documents", "extracted_fields",
        "findings", "audit_events", "verification_attempts", "rule_settings", "clarifications",
    ):
        if table == "users":
            source.execute("CREATE TABLE users (email TEXT)")
        elif table == "documents":
            source.execute("CREATE TABLE documents (id INTEGER, storage_name TEXT, hash TEXT)")
        else:
            source.execute(f'CREATE TABLE "{table}" (id INTEGER)')
    source.execute("INSERT INTO users VALUES ('submitter@example.edu')")
    _assert_source_ready(source)
    source.execute("UPDATE users SET email='submitter@northwind.demo'")
    with pytest.raises(MigrationError, match="synthetic .demo"):
        _assert_source_ready(source)

    source.execute("UPDATE users SET email='submitter@example.edu'")
    root = tmp_path / "uploads"
    root.mkdir()
    (root / "document.pdf").write_bytes(b"verified content")
    source.execute("INSERT INTO documents VALUES (?, ?, ?)", (1, "document.pdf", sha256(b"verified content")))
    assert len(_verify_document_files(source, root)) == 1
    source.execute("UPDATE documents SET hash='wrong'")
    with pytest.raises(MigrationError, match="SHA-256"):
        _verify_document_files(source, root)
    source.execute("UPDATE documents SET storage_name='../outside.pdf'")
    with pytest.raises(MigrationError, match="unsafe storage key"):
        _verify_document_files(source, root)
    source.close()

def test_sqlite_migration_requires_postgres_target_and_upload_root(tmp_path, monkeypatch):
    from scripts.migrate_sqlite_to_postgres import MigrationError, migrate

    monkeypatch.setenv("APP_DATABASE_URL", "sqlite:///not-postgres")
    monkeypatch.setenv("APP_UPLOAD_DIR", str(tmp_path / "uploads"))
    with pytest.raises(MigrationError, match="empty PostgreSQL target"):
        migrate(tmp_path / "missing.db", tmp_path / "uploads")

def test_local_admin_cannot_assign_an_oidc_subject():
    admin = headers("admin@arbiter.demo", "Admin@123")
    response = client.post("/api/admin/users", headers=admin, json={
        "email": "new.user@example.edu", "role": "reviewer",
        "initial_password": "Initial-Secret-123", "oidc_subject": "provider-subject-123",
    })
    assert response.status_code == 400

def test_oidc_rejects_verified_email_without_prebound_subject(monkeypatch):
    import asyncio
    from starlette.requests import Request
    from app import main

    class FakeOAuthClient:
        async def authorize_access_token(self, request):
            return {"userinfo": {"email": "submitter@northwind.demo", "email_verified": True, "sub": "unbound-subject"}}

    scope = {"type": "http", "method": "GET", "headers": [], "query_string": b"", "server": ("test", 80), "scheme": "http", "path": "/api/auth/oidc/callback", "root_path": "", "http_version": "1.1"}
    monkeypatch.setattr(main, "AUTH_MODE", "oidc")
    monkeypatch.setattr(main, "oidc_client", lambda: FakeOAuthClient())
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.oidc_callback(Request(scope)))
    assert error.value.status_code == 403

def test_production_configuration_requires_real_external_services(monkeypatch):
    import app.main as main
    from app.main import production_configuration_errors

    ready = {
        "APP_BOOTSTRAP_DEMO_DATA": "false",
        "APP_DATABASE_URL": "postgresql://arbiter:secret@db.example.edu/arbiter",
        "APP_STORAGE_BACKEND": "s3",
        "APP_S3_BUCKET": "arbiter-private",
        "APP_S3_REGION": "ap-south-1",
        "APP_S3_ENCRYPTION": "aws:kms",
        "APP_S3_KMS_KEY_ID": "arn:aws:kms:ap-south-1:123456789012:key/test",
        "APP_AUTH_MODE": "oidc",
        "APP_OIDC_ISSUER": "https://identity.example.edu",
        "APP_OIDC_CLIENT_ID": "arbiter",
        "APP_OIDC_CLIENT_SECRET": "provider-secret",
        "APP_OIDC_ALLOWED_DOMAINS": "example.edu",
        "APP_SESSION_COOKIE_SECRET": "s" * 64,
        "APP_MALWARE_SCAN_MODE": "clamav",
        "APP_CLAMAV_HOST": "clamav.internal",
        "APP_ALLOWED_HOSTS": "arbiter.example.edu",
        "APP_ALLOWED_ORIGINS": "https://arbiter.example.edu",
    }
    monkeypatch.setitem(main.RULE_PACK, "provisional", False)
    assert production_configuration_errors(ready) == []
    monkeypatch.setitem(main.RULE_PACK, "provisional", True)
    assert any("rule pack is provisional" in error for error in production_configuration_errors(ready))
    errors = production_configuration_errors({})
    assert any("APP_DATABASE_URL" in error for error in errors)
    assert any("APP_STORAGE_BACKEND" in error for error in errors)
    assert any("APP_OIDC_ISSUER" in error for error in errors)
    assert any("APP_ALLOWED_ORIGINS" in error for error in errors)

def test_production_module_import_does_not_initialize_database_before_validation(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "must-not-be-created.db"
    uploads = tmp_path / "must-not-be-created-uploads"
    env = os.environ.copy()
    env.update({
        "APP_ENV": "production",
        "APP_DATABASE_URL": "",
        "APP_DATABASE_PATH": str(database),
        "APP_UPLOAD_DIR": str(uploads),
        "APP_STORAGE_BACKEND": "local",
        "APP_BOOTSTRAP_DEMO_DATA": "false",
    })
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not database.exists()
    assert not uploads.exists()

def test_production_lifespan_rejects_invalid_configuration_before_db_init(monkeypatch):
    import asyncio
    import app.main as main

    monkeypatch.setattr(main, "APP_ENV", "production")
    monkeypatch.setenv("APP_BOOTSTRAP_DEMO_DATA", "false")
    monkeypatch.setattr(main, "init_db", lambda: pytest.fail("database init ran before production validation"))

    async def start_app():
        async with main.app.router.lifespan_context(main.app):
            pass

    with pytest.raises(RuntimeError, match="Invalid production configuration"):
        asyncio.run(start_app())

def test_malware_scan_is_explicitly_marked_when_disabled(monkeypatch):
    monkeypatch.setenv("APP_MALWARE_SCAN_MODE", "disabled")
    result = scan(b"synthetic document")
    assert result.status == "not_configured"

def test_configured_clamav_readiness_uses_framed_ping(monkeypatch):
    import app.malware as malware

    class FakeSocket:
        def __init__(self, reply): self.reply = reply; self.sent = bytearray()
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def sendall(self, data): self.sent.extend(data)
        def recv(self, size): chunk, self.reply = self.reply[:size], self.reply[size:]; return chunk

    fake = FakeSocket(b"PONG\0")
    monkeypatch.setenv("APP_MALWARE_SCAN_MODE", "clamav")
    monkeypatch.setattr(malware.socket, "create_connection", lambda *args, **kwargs: fake)
    malware.check()
    assert bytes(fake.sent) == b"zPING\0"

    monkeypatch.setattr(malware.socket, "create_connection", lambda *args, **kwargs: FakeSocket(b"NOPE\0"))
    with pytest.raises(MalwareScanUnavailable):
        malware.check()

@pytest.mark.parametrize(("response", "expected"), [
    (b"stream: OK\0", "clean"),
    (b"stream: Eicar-Signature FOUND\0", "infected"),
])
def test_clamav_stream_protocol_reply_is_parsed(monkeypatch, response, expected):
    import app.malware as malware

    class FakeSocket:
        def __init__(self): self.sent = bytearray(); self.reply = response
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def sendall(self, data): self.sent.extend(data)
        def recv(self, size):
            chunk, self.reply = self.reply[: min(size, 5)], self.reply[min(size, 5):]
            return chunk

    fake = FakeSocket()
    monkeypatch.setenv("APP_MALWARE_SCAN_MODE", "clamav")
    monkeypatch.setattr(malware.socket, "create_connection", lambda *args, **kwargs: fake)
    result = scan(b"abc")
    assert result.status == expected
    assert bytes(fake.sent) == b"zINSTREAM\0" + b"\0\0\0\x03abc" + b"\0\0\0\0"

def test_clamav_scan_fails_closed_on_unterminated_reply(monkeypatch):
    import app.malware as malware

    class FakeSocket:
        def __init__(self): self.read = False
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def sendall(self, data): pass
        def recv(self, size):
            if self.read: return b""
            self.read = True
            return b"stream: OK"

    monkeypatch.setenv("APP_MALWARE_SCAN_MODE", "clamav")
    monkeypatch.setattr(malware.socket, "create_connection", lambda *args, **kwargs: FakeSocket())
    with pytest.raises(MalwareScanUnavailable):
        scan(b"abc")

def test_operational_readiness_and_audit_policy_are_available_to_admin():
    assert client.get("/api/health/live").json()["status"] == "live"
    assert client.get("/api/health/ready").json()["status"] == "ready"
    admin = headers("admin@arbiter.demo", "Admin@123")
    policy = client.get("/api/operations/audit-retention", headers=admin).json()
    assert policy["automatic_deletion"] is False
    assert policy["retention_days"] == 2555
    assert policy["policy_status"] == "demo_default_unapproved"
    assert policy["policy_scope"] == "audit_events_only"

def test_local_identity_mode_and_admin_directory_are_available():
    assert client.get("/api/auth/mode").json()["mode"] == "local"
    admin = headers("admin@arbiter.demo", "Admin@123")
    users = client.get("/api/admin/users", headers=admin).json()
    assert any(user["email"] == "submitter@northwind.demo" and user["active"] for user in users)

def test_document_replacement_preserves_a_version_history_and_audit_chain():
    client.post("/api/applications/2/clarifications", headers=OFFICER, json={"message":"Please provide a corrected declaration."})
    case = client.get("/api/applications/2", headers=APPLICANT).json()
    current = next(document for document in case["documents"] if document["document_type"] == "declaration" and not document["superseded"])
    response = client.post(f"/api/applications/2/documents?document_type=declaration&replaces_document_id={current['id']}", headers=APPLICANT, files={"file": ("replacement.txt", b"Organization Name: Cascade Chemicals Ltd\nCertificate No: DECL-NEW\n", "text/plain")})
    assert response.status_code == 200
    versions = client.get(f"/api/documents/{response.json()['document_id']}/versions", headers=APPLICANT).json()
    assert any(version["id"] == current["id"] for version in versions)
    admin = headers("admin@arbiter.demo", "Admin@123")
    verification = client.get("/api/applications/2/audit/verify", headers=admin).json()
    assert verification["valid"] is True

def test_clarification_response_round_trip():
    created = client.post("/api/applications/2/clarifications", headers=OFFICER, json={"message": "Please clarify the affiliation record."})
    assert created.status_code == 200
    case = client.get("/api/applications/2", headers=OFFICER).json()
    clarification = case["clarifications"][0]
    response = client.post(f"/api/clarifications/{clarification['id']}/respond", headers=APPLICANT, json={"message": "Updated document uploaded; please review."})
    assert response.status_code == 200
    case = client.get("/api/applications/2", headers=APPLICANT).json()
    assert case["clarifications"][0]["status"] == "responded"

def test_cross_document_field_contradiction_detects_mismatch_between_two_documents():
    client.post("/api/applications/2/precheck", headers=OFFICER)
    case = client.get("/api/applications/2", headers=OFFICER).json()
    contradiction = next((f for f in case["findings"] if f["rule_id"] == "FIELD-CONFLICT-001"), None)
    assert contradiction is not None
    assert contradiction["kind"] == "data_inconsistency"
    assert contradiction["severity"] == "high"
    assert "vs" in contradiction["evidence"]
    assert "Cascade Chemical Works" in contradiction["evidence"]
    assert "Cascade Chemicals Ltd" in contradiction["evidence"]

def test_extracted_fields_persist_and_return_verbatim_quotes():
    response = client.post(
        "/api/applications/1/documents?document_type=safety_certificate",
        headers=headers("submitter@northwind.demo", "Submitter@123"),
        files={"file": ("quote_fire.txt", b"Organization Name: Northwind Logistics Ltd\nCertificate No: SAFE-Q-99\nValid Until: 2029-12-31\n", "text/plain")},
    )
    assert response.status_code == 200
    doc_id = response.json()["document_id"]
    case = client.get("/api/applications/1", headers=headers("submitter@northwind.demo", "Submitter@123")).json()
    doc = next(d for d in case["documents"] if d["id"] == doc_id)
    fields = {f["field_name"]: f for f in doc["fields"]}
    assert "organization_name" in fields
    assert fields["organization_name"]["quote"] == "Organization Name: Northwind Logistics Ltd"
    assert "certificate_number" in fields
    assert fields["certificate_number"]["quote"] == "Certificate No: SAFE-Q-99"
    assert "valid_until" in fields
    assert fields["valid_until"]["quote"] == "Valid Until: 2029-12-31"

def test_document_page_endpoint_returns_page_content_and_metadata():
    case = client.get("/api/applications/1", headers=headers("submitter@northwind.demo", "Submitter@123")).json()
    doc_id = case["documents"][0]["id"]
    page_res = client.get(f"/api/documents/{doc_id}/pages/1", headers=OFFICER)
    assert page_res.status_code == 200
    data = page_res.json()
    assert data["document_id"] == doc_id
    assert data["page"] == 1
    assert data["total_pages"] >= 1
    assert "Northwind" in data["text"]
    # Check invalid page yields 404
    assert client.get(f"/api/documents/{doc_id}/pages/99", headers=OFFICER).status_code == 404

def test_sanctioned_vs_requested_capacity_discrepancy_rule():
    app_headers = headers("submitter@northwind.demo", "Submitter@123")
    # Set requested capacity to 180
    client.patch("/api/applications/1", headers=app_headers, json={
        "organization_name": "Northwind Logistics Ltd",
        "review_cycle": "2026-27",
        "request_type": "licence_renewal",
        "requested_capacity": 180,
    })
    # Upload document with stated capacity 60
    uploaded = client.post(
        "/api/applications/1/documents?document_type=prior_approval",
        headers=app_headers,
        files={"file": ("scenario_6_capacity.txt", b"Organization Name: Northwind Logistics Ltd\nSanctioned Capacity: 60\nReference No: SUB-CAP-60", "text/plain")},
    )
    assert uploaded.status_code == 200
    client.post("/api/applications/1/precheck", headers=OFFICER)
    case = client.get("/api/applications/1", headers=OFFICER).json()
    finding = next((f for f in case["findings"] if f["rule_id"] == "CAPACITY-MATCH-001" and f["document_id"] == uploaded.json()["document_id"]), None)
    assert finding is not None
    assert finding["kind"] == "data_inconsistency"
    assert "Application requested: 180" in finding["evidence"]
    assert "Document stated: 60" in finding["evidence"]

def test_scope_item_mismatch_rule():
    app_headers = headers("submitter@northwind.demo", "Submitter@123")
    # Set the application scope item to Hazardous Waste Handling
    client.patch("/api/applications/1", headers=app_headers, json={
        "organization_name": "Northwind Logistics Ltd",
        "review_cycle": "2026-27",
        "request_type": "licence_renewal",
        "scope_name": "Hazardous Waste Handling",
    })
    # Upload a document that states a different scope item
    uploaded = client.post(
        "/api/applications/1/documents?document_type=prior_approval",
        headers=app_headers,
        files={"file": ("scope_mismatch.txt", b"Organization Name: Northwind Logistics Ltd\nApproved Scope item: Bulk Storage Operations\nReference No: SUB-SC-01", "text/plain")},
    )
    assert uploaded.status_code == 200
    client.post("/api/applications/1/precheck", headers=OFFICER)
    case = client.get("/api/applications/1", headers=OFFICER).json()
    finding = next((f for f in case["findings"] if f["rule_id"] == "SCOPE-MATCH-001" and f["document_id"] == uploaded.json()["document_id"]), None)
    assert finding is not None
    assert finding["kind"] == "data_inconsistency"
    assert "Bulk Storage Operations" in finding["evidence"]




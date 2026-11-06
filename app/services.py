from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from . import ai
from .rules import CHECKLIST, DEMO_REVIEW_CYCLES, RULE_BY_ID, RULE_PACK, normalize

MAX_PDF_PAGES = int(os.getenv("APP_MAX_PDF_PAGES", "25"))


def select_rule_pack(conn, application):
    """Return the newest immutable published pack whose declared scope covers this case."""
    import json
    try:
        versions = conn.execute("SELECT version,snapshot_json FROM rule_pack_versions WHERE status IN ('published','superseded') ORDER BY id").fetchall()
        year, request_type = application["review_cycle"], application["request_type"]
        for row in reversed(versions):
            payload = json.loads(row["snapshot_json"])
            if isinstance(payload, dict) and not any(key in payload for key in ("rules", "review_cycles", "request_types", "effective_from", "effective_to")):
                payload = {"rules": payload}
            years = payload.get("review_cycles", DEMO_REVIEW_CYCLES)
            request_types = payload.get("request_types", tuple(CHECKLIST))
            if (year in years and request_type in request_types
                    and (not payload.get("effective_from") or year >= payload["effective_from"])
                    and (not payload.get("effective_to") or year <= payload["effective_to"])):
                return row, payload
        return None, {}
    except Exception:
        return None, {}

def rule_enabled(conn, rule_id: str, application=None) -> bool:
    """Rules default to enabled only when registry lookup succeeds and scope applies."""
    try:
        if application is not None:
            selected, payload = select_rule_pack(conn, application)
            rule = RULE_BY_ID.get(rule_id, {})
            if (application["review_cycle"] not in rule.get("review_cycles", ())
                    or application["request_type"] not in rule.get("request_types", ())):
                return False
            if selected:
                snapshot = payload.get("rules", payload)
                if rule_id in snapshot: return bool(snapshot[rule_id])
        row = conn.execute("SELECT enabled FROM rule_settings WHERE rule_id = ?", (rule_id,)).fetchone()
        return bool(row["enabled"]) if row else True
    except Exception:
        # On unexpected registry failure, fail closed for rule execution.
        return False

def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def inspect_pdf_signature_fields(content: bytes) -> dict:
    """Report interactive PDF signature-field cues only; never claim crypto verification."""
    try:
        import pymupdf
        with pymupdf.open(stream=content, filetype="pdf") as doc:
            fields = []
            for page_number, page in enumerate(doc, start=1):
                for widget in page.widgets() or ():
                    if widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                        value = str(widget.field_value or "").strip()
                        fields.append({
                            "field_name": widget.field_name or "Unnamed signature field",
                            "page": page_number,
                            "filled": bool(value),
                        })
            return {
                "status": "signature_field_present_unverified" if fields else "no_signature_field_detected",
                "signature_fields": fields,
                "detail": (
                    "Interactive signature field(s) detected. This is only a structural cue; signer identity, certificate trust, revocation and signed-byte integrity were not validated."
                    if fields else
                    "No interactive PDF signature field was detected. Visible signatures, QR codes and external source records were not checked."
                ),
            }
    except Exception:
        return {
            "status": "inspection_unavailable",
            "signature_fields": [],
            "detail": "PDF signature-field inspection could not be completed; manual review is required.",
        }

def extract_text(filename: str, content: bytes) -> tuple[str, float, str | None]:
    if filename.lower().endswith((".txt", ".csv")):
        return content.decode("utf-8", errors="replace"), 0.98, None
    if filename.lower().endswith(".pdf"):
        pages, confidence, error = extract_page_texts(filename, content)
        return "\n".join(pages), confidence, error
    return "", 0.0, "Unsupported extraction format; manual review required"

def extract_page_texts(filename: str, content: bytes) -> tuple[list[str], float, str | None]:
    """Extract text per page so evidence locations are not fabricated as page 1."""
    if filename.lower().endswith((".txt", ".csv")):
        return [content.decode("utf-8", errors="replace")], 0.98, None
    if filename.lower().endswith((".png", ".jpg", ".jpeg")):
        tesseract = shutil.which("tesseract")
        if not tesseract:
            return [], 0.0, "Infrastructure image stored; OCR is unavailable, so visual reviewer review is required"
        try:
            with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix.lower()) as image_file:
                image_file.write(content)
                image_file.flush()
                result = subprocess.run(
                    [tesseract, image_file.name, "stdout", "-l", os.getenv("APP_OCR_LANGUAGE", "eng")],
                    capture_output=True, text=True, timeout=45, check=False,
                )
            if result.returncode != 0:
                return [], 0.0, "Infrastructure image OCR failed; visual reviewer review is required"
            return [result.stdout], 0.60, "Image OCR is advisory; visually review the original facility evidence"
        except Exception:
            return [], 0.0, "Infrastructure image OCR failed; visual reviewer review is required"
    if filename.lower().endswith(".pdf"):
        try:
            import pymupdf
            with pymupdf.open(stream=content, filetype="pdf") as doc:
                if doc.page_count > MAX_PDF_PAGES:
                    return [], 0.0, f"PDF exceeds the {MAX_PDF_PAGES}-page processing limit; manual review required"
                pages = [page.get_text() for page in doc]
                if all(page.strip() for page in pages):
                    return pages, 0.84, None
                tesseract = shutil.which("tesseract")
                if not tesseract:
                    if any(page.strip() for page in pages):
                        return pages, 0.84, "Some pages contain no embedded text; install Tesseract for OCR or review those pages manually"
                    return [], 0.0, "PDF contains no extractable embedded text; manual review required"
                ocr_used = False
                for index, page in enumerate(doc):
                    if pages[index].strip():
                        continue
                    image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png")
                    with tempfile.NamedTemporaryFile(suffix=".png") as image_file:
                        image_file.write(image)
                        image_file.flush()
                        result = subprocess.run(
                            [tesseract, image_file.name, "stdout", "-l", os.getenv("APP_OCR_LANGUAGE", "eng")],
                            capture_output=True, text=True, timeout=45, check=False,
                        )
                    if result.returncode == 0:
                        pages[index] = result.stdout
                        ocr_used = True
                if any(page.strip() for page in pages):
                    if ocr_used:
                        return pages, 0.65, "OCR-derived text requires human confirmation"
                    return pages, 0.84, None
            return [], 0.0, "PDF contains no extractable embedded text; manual review required"
        except Exception:
            return [], 0.0, "Unreadable PDF; manual review required"
    return [], 0.0, "Unsupported extraction format; manual review required"

def extract_fields_with_pages(pages: list[str]) -> list[tuple[str, str, int]]:
    return [(field, value, page_number)
            for page_number, page_text in enumerate(pages, start=1)
            for field, value in extract_fields(page_text).items()]

def extract_fields_and_quotes(text: str) -> dict[str, tuple[str, str]]:
    patterns = {
        "organization_name": r"(?:Organization(?: Name)?|Name of (?:the )?(?:Organization|Entity|Company|Applicant)|Issued to|Applicant)\s*[:=-]\s*([^\n]+)",
        "valid_until": r"(?:Valid(?:ity)?(?: Until| Through)?|Expiry Date)\s*(?:[:=-]\s*|\s+)(\d{4}-\d{2}-\d{2})",
        "scope_name": r"(?:Approved\s+)?(?:Scope(?: item)?|Service|Product|Category)\s*(?:Name)?\s*[:=-]\s*([^\n]+)",
        "capacity": r"(?:Approved\s+Capacity|Sanctioned\s+Capacity|Capacity(?:\s+Limit)?)\s*[:=-]\s*(\d+)",
    }
    fields = {}
    for key, pattern in patterns.items():
        if match := re.search(pattern, text, re.I):
            fields[key] = (match.group(1).strip(), match.group(0).strip())
    number_label = r"(?:Certificate(?:\s+serial)?|(?:Prior\s+)?Reference|Consent\s+Reference|Document\s+Reference|Serial|Registration|Letter|Declaration)"
    number_pattern = rf"{number_label}\s*(?:No\.?|Number)?(?:\s*[:=#-]\s*|\s+)([A-Z0-9][A-Z0-9/._-]*\d[A-Z0-9/._-]*)"
    number_next_line = rf"{number_label}\s*(?:No\.?|Number)?\s*\n\s*([A-Z0-9][A-Z0-9/._-]*\d[A-Z0-9/._-]*)"
    number_match = re.search(number_pattern, text, re.I) or re.search(number_next_line, text, re.I)
    if number_match:
        fields["certificate_number"] = (number_match.group(1).strip(), number_match.group(0).strip())
    return fields

def extract_fields_with_pages_and_quotes(pages: list[str]) -> list[dict]:
    return [
        {"field_name": field, "raw_value": val_quote[0], "normalized_value": val_quote[0],
         "page": page_number, "quote": val_quote[1]}
        for page_number, page_text in enumerate(pages, start=1)
        for field, val_quote in extract_fields_and_quotes(page_text).items()
    ]

def extract_fields(text: str) -> dict[str, str]:
    return {k: v[0] for k, v in extract_fields_and_quotes(text).items()}

def create_finding(conn, application_id: int, kind: str, severity: str, title: str, explanation: str,
                   document_id: int | None = None, evidence: str | None = None, rule_id: str | None = None,
                   evidence_page: int | None = None):
    conn.execute(
        """INSERT INTO findings(application_id, document_id, kind, severity, title, explanation, evidence, rule_id, rule_version, status, evidence_page)
           VALUES(?,?,?,?,?,?,?,?,?, 'open', ?)""",
        (application_id, document_id, kind, severity, title, explanation, evidence, rule_id,
         RULE_BY_ID.get(rule_id, {}).get("source_version", RULE_PACK["version"]) if rule_id else None, evidence_page),
    )

def run_checks(conn, application_id: int) -> int:
    conn.execute("DELETE FROM findings WHERE application_id = ? AND status = 'open'", (application_id,))
    app = conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    documents = conn.execute("SELECT * FROM documents WHERE application_id = ? AND NOT EXISTS(SELECT 1 FROM documents newer WHERE newer.supersedes_document_id=documents.id)", (application_id,)).fetchall()
    by_type = {d["document_type"]: d for d in documents}
    for item in CHECKLIST[app["request_type"]]:
        if not rule_enabled(conn, "DOCS-REQUIRED-001", app):
            break
        if item["required"] and item["type"] not in by_type:
            create_finding(conn, application_id, "compliance_check", "high", f"Missing: {item['label']}",
                           "The selected checklist marks this document as required for this request type.",
                           rule_id="DOCS-REQUIRED-001")
    seen_hashes: dict[str, int] = {}
    for d in documents:
        if rule_enabled(conn, "DUPLICATE-001", app) and d["hash"] in seen_hashes:
            create_finding(conn, application_id, "suspicious_indicator", "medium", "Exact duplicate document",
                           "This file has the same cryptographic hash as another file in this application. It is a review signal, not proof of wrongdoing.",
                           d["id"], f"Matches document #{seen_hashes[d['hash']]}", "DUPLICATE-001")
        seen_hashes[d["hash"]] = d["id"]
        if rule_enabled(conn, "EXTRACT-001", app) and d["extraction_error"]:
            create_finding(conn, application_id, "processing_uncertainty", "medium", "Extraction needs manual review",
                           d["extraction_error"], d["id"], "No reliable text extraction", "EXTRACT-001")
        elif rule_enabled(conn, "EXTRACT-001", app) and ai.assistance_attempted():
            ai_result = conn.execute("SELECT status, detail FROM document_ai_analysis WHERE document_id = ?", (d["id"],)).fetchone()
            if ai_result and ai_result["status"] == "manual_review":
                create_finding(conn, application_id, "processing_uncertainty", "medium", "AI assistance needs manual review",
                               ai_result["detail"], d["id"], "No reliable AI suggestion; confirm the original document.", "EXTRACT-001")
        if rule_enabled(conn, "EXTRACT-002", app) and d["confidence"] < 0.7:
            create_finding(conn, application_id, "processing_uncertainty", "medium", "Low extraction confidence",
                           "Critical values extracted with low confidence must be confirmed by a person.", d["id"],
                           f"Confidence: {d['confidence']:.0%}", "EXTRACT-002")
        field_rows = {r["field_name"]: r for r in conn.execute("SELECT field_name, normalized_value, page FROM extracted_fields WHERE document_id = ?", (d["id"],))}
        fields = {name: row["normalized_value"] for name, row in field_rows.items()}
        doc_name = fields.get("organization_name")
        if rule_enabled(conn, "NAME-MATCH-001", app) and doc_name and normalize(doc_name) != normalize(app["organization_name"]):
            create_finding(conn, application_id, "data_inconsistency", "high", "Organization name mismatch",
                           "The document's extracted organization name differs from the application record after basic normalization.",
                           d["id"], f"Application: {app['organization_name']} | Document: {doc_name} | Source page: {field_rows['organization_name']['page']}", "NAME-MATCH-001", field_rows["organization_name"]["page"])
        expiry = fields.get("valid_until")
        if rule_enabled(conn, "EXPIRY-001", app) and expiry:
            try:
                if date.fromisoformat(expiry) < date.today():
                    create_finding(conn, application_id, "compliance_check", "high", "Document appears expired",
                                   "The extracted validity date is before today's assessment date. Confirm against the applicable rule and original document.",
                                   d["id"], f"Valid until: {expiry} | Source page: {field_rows['valid_until']['page']}", "EXPIRY-001", field_rows["valid_until"]["page"])
            except ValueError:
                pass
        doc_prog = fields.get("scope_name")
        app_prog = app["scope_name"]
        if rule_enabled(conn, "SCOPE-MATCH-001", app) and doc_prog and app_prog:
            if normalize(doc_prog) != normalize(app_prog) and normalize(doc_prog) not in normalize(app_prog) and normalize(app_prog) not in normalize(doc_prog):
                create_finding(conn, application_id, "data_inconsistency", "medium", "Scope item mismatch",
                               "The scope item extracted from the document differs from the application record.",
                               d["id"], f"Application: {app_prog} | Document: {doc_prog} | Source page: {field_rows['scope_name']['page']}", "SCOPE-MATCH-001", field_rows["scope_name"]["page"])
        doc_capacity = fields.get("capacity")
        app_capacity = app["requested_capacity"]
        if rule_enabled(conn, "CAPACITY-MATCH-001", app) and doc_capacity and app_capacity is not None:
            try:
                if int(doc_capacity) != int(app_capacity):
                    create_finding(conn, application_id, "data_inconsistency", "high", "Sanctioned vs requested capacity discrepancy",
                                   "The document's stated capacity differs from the capacity requested in the application.",
                                   d["id"], f"Application requested: {app_capacity} | Document stated: {doc_capacity} | Source page: {field_rows['capacity']['page']}", "CAPACITY-MATCH-001", field_rows["capacity"]["page"])
            except ValueError:
                pass
    if rule_enabled(conn, "FIELD-CONFLICT-001", app) and len(documents) > 1:
        doc_fields = {}
        for d in documents:
            rows = conn.execute("SELECT field_name, raw_value, normalized_value, page, quote FROM extracted_fields WHERE document_id = ?", (d["id"],)).fetchall()
            doc_fields[d["id"]] = {r["field_name"]: r for r in rows}
        for i in range(len(documents)):
            for j in range(i + 1, len(documents)):
                doc_a = documents[i]
                doc_b = documents[j]
                fa = doc_fields.get(doc_a["id"], {})
                fb = doc_fields.get(doc_b["id"], {})
                name_a = fa.get("organization_name")
                name_b = fb.get("organization_name")
                if name_a and name_b and normalize(name_a["normalized_value"]) != normalize(name_b["normalized_value"]):
                    ev_a = f"Doc #{doc_a['id']} ({doc_a['document_type']}, p.{name_a['page']}): '{name_a['raw_value']}'"
                    ev_b = f"Doc #{doc_b['id']} ({doc_b['document_type']}, p.{name_b['page']}): '{name_b['raw_value']}'"
                    create_finding(conn, application_id, "data_inconsistency", "high", "Cross-document organization name contradiction",
                                   "Two uploaded documents in this application state contradictory organization names. Review both source documents to determine the correct entity.",
                                   doc_a["id"], f"{ev_a} vs {ev_b}", "FIELD-CONFLICT-001", name_a["page"])
    count = conn.execute("SELECT count(*) AS count FROM findings WHERE application_id = ? AND status = 'open'", (application_id,)).fetchone()["count"]
    conn.execute("UPDATE applications SET status = ? WHERE id = ?", ("deficiencies_identified" if count else "ready_for_review", application_id))
    conn.commit()
    return count


"""
create_demo_pdf.py - Scratch script to generate a synthetic multi-page PDF for the
Icarus OCR / Tesseract pipeline demo.

Usage:
    .venv\\Scripts\\python.exe scripts\\create_demo_pdf.py

Output:
    docs/demo_data/scenario_ocr_scanned_demo.pdf
    - Page 1: Cover page (embedded text, no OCR needed)
    - Page 2: Typed organization details (embedded text)
    - Page 3: Image-only page simulating a scanned document (requires Tesseract OCR)

The image-only page (page 3) is created by rendering text onto a pixmap and embedding
the resulting image, so no embedded text layer is present. This exercises the
PyMuPDF → Tesseract OCR fallback path in app/services.py.
"""

import pathlib
import sys
import datetime

try:
    import pymupdf  # PyMuPDF ≥ 1.23 uses this import name
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        print("ERROR: pymupdf not installed. Run: pip install pymupdf")
        sys.exit(1)

OUTPUT_PATH = pathlib.Path(__file__).parent.parent / "docs" / "demo_data" / "scenario_ocr_scanned_demo.pdf"

# ---------------------------------------------------------------------------
# Page 1 - Cover page (digital text layer)
# ---------------------------------------------------------------------------
def add_cover_page(doc: pymupdf.Document) -> None:
    page = doc.new_page(width=595, height=842)  # A4
    tw = pymupdf.TextWriter(page.rect)

    title_font = pymupdf.Font("helv")
    body_font  = pymupdf.Font("helv")

    tw.append((72, 120), "Icarus - Sample Application File", font=title_font, fontsize=18)
    tw.append((72, 160), "Synthetic demo document - not a real record", font=body_font, fontsize=12)
    tw.append((72, 200), f"Generated: {datetime.date.today().isoformat()}", font=body_font, fontsize=11)
    tw.append((72, 240), "Organization: Northwind Logistics Ltd", font=body_font, fontsize=11)
    tw.append((72, 270), "Reference Number: SUB-2026-1001", font=body_font, fontsize=11)
    tw.append((72, 310), "IMPORTANT: This is a synthetic test document.", font=body_font, fontsize=10)
    tw.append((72, 330), "Do not use real organizational or personal data.", font=body_font, fontsize=10)
    tw.append((72, 370), "Page 1 of 3 - Cover (embedded text, no OCR required)", font=body_font, fontsize=9)
    tw.write_text(page)


# ---------------------------------------------------------------------------
# Page 2 - Structured fields (digital text layer)
# ---------------------------------------------------------------------------
def add_fields_page(doc: pymupdf.Document) -> None:
    page = doc.new_page(width=595, height=842)
    tw = pymupdf.TextWriter(page.rect)
    font = pymupdf.Font("helv")

    lines = [
        ("Organization Name:", "Northwind Logistics Ltd"),
        ("Address:",          "Plot 47, MIDC Industrial Area, Pune - 411019"),
        ("Approved Scope:",    "Bulk Storage"),
        ("Sanctioned Capacity:","120"),
        ("Requested Capacity:", "120"),
        ("Review Cycle:",    "2026-27"),
        ("Application Date:", "2026-10-01"),
    ]

    tw.append((72, 80), "Application Details - Page 2 of 3", font=font, fontsize=14)
    y = 130
    for label, value in lines:
        tw.append((72,  y), label, font=font, fontsize=11)
        tw.append((220, y), value, font=font, fontsize=11)
        y += 32

    tw.append((72, y + 20), "Page 2 of 3 - Structured fields (embedded text, no OCR required)", font=font, fontsize=9)
    tw.write_text(page)


# ---------------------------------------------------------------------------
# Page 3 - Image-only page simulating a scan (no embedded text)
# ---------------------------------------------------------------------------
def add_scanned_page(doc: pymupdf.Document) -> None:
    """
    Render text onto a Pixmap and insert the resulting image as a page-filling
    figure. The resulting page has NO embedded text layer, so Tesseract OCR
    is required to extract content from it.
    """
    # Create a pixmap and write text using draw_string (low-level)
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 595, 842))
    pix.set_dpi(150, 150)
    pix.clear_with(255)  # white background

    # Draw text using shape on a temporary single-page document
    tmp_doc = pymupdf.open()
    tmp_page = tmp_doc.new_page(width=595, height=842)
    tw = pymupdf.TextWriter(tmp_page.rect)
    font = pymupdf.Font("helv")

    scanned_lines = [
        "SAFETY AND EMERGENCY ACCESS CERTIFICATE",
        "Certificate Serial: SAFE/SCAN/2026/00099",
        "",
        "Organization Name: Northwind Logistics Ltd",
        "Address: Plot 47, MIDC Industrial Area, Pune - 411019",
        "",
        "Inspection Date: 10 October 2026",
        "Valid Until: 09 October 2029",
        "",
        "Issued By: Sample Fire & Emergency Services (fictional)",
        "Authorised By: District Safety Reviewer",
        "",
        "*** SIMULATED SCANNED PAGE - OCR DEMO ***",
        "This page has no embedded text. OCR is required.",
        "Page 3 of 3",
    ]

    y = 80
    for line in scanned_lines:
        if line:
            tw.append((72, y), line, font=font, fontsize=12)
        y += 36

    tw.write_text(tmp_page)

    # Render the temp page to a pixmap (simulate a scan)
    mat = pymupdf.Matrix(1.5, 1.5)  # 150% scale → ~150 DPI equivalent
    rendered = tmp_page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY)
    tmp_doc.close()

    # Add a new page in the main doc and insert the image
    page = doc.new_page(width=595, height=842)
    img_rect = pymupdf.Rect(0, 0, 595, 842)
    page.insert_image(img_rect, pixmap=rendered)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open()
    add_cover_page(doc)
    add_fields_page(doc)
    add_scanned_page(doc)

    doc.save(str(OUTPUT_PATH))
    doc.close()

    print(f"Created: {OUTPUT_PATH}")
    print("  Page 1: Cover (digital text)")
    print("  Page 2: Structured fields (digital text)")
    print("  Page 3: Image-only scan simulation (Tesseract required for OCR)")
    print()
    print("To test OCR extraction, upload this PDF in the staging container")
    print("(port 8002 by default, which includes Tesseract) via the Submitter document upload UI.")


if __name__ == "__main__":
    main()

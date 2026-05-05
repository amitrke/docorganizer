from pathlib import Path


def _ocr_page_text(page) -> str:
    """Render a PDF page to an image and run OCR if dependencies are available."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""

    # 200 DPI is usually enough for OCR while keeping memory reasonable.
    pix = page.get_pixmap(dpi=200, alpha=False)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    try:
        return pytesseract.image_to_string(image).strip()
    except Exception:
        # If Tesseract binary is missing or OCR fails, keep pipeline non-blocking.
        return ""


def extract_text(pdf_path: str | Path) -> str:
    """
    Extract plain text from a PDF using PyMuPDF.

    If no selectable text is found, attempt OCR fallback with pytesseract
    (when optional OCR dependencies are installed).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is not installed. Run: pip install pymupdf"
        ) from exc

    text_parts: list[str] = []
    ocr_parts: list[str] = []

    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            page_text = page.get_text().strip()
            if page_text:
                text_parts.append(page_text)
                continue

            # OCR only for pages without selectable text.
            ocr_text = _ocr_page_text(page)
            if ocr_text:
                ocr_parts.append(ocr_text)

    merged = text_parts + ocr_parts
    return "\n".join(merged).strip()

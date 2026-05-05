from pathlib import Path


def extract_text(pdf_path: str | Path) -> str:
    """
    Extract plain text from a PDF using PyMuPDF.
    Returns an empty string if the PDF has no selectable text
    (OCR fallback is handled in Phase 2).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is not installed. Run: pip install pymupdf"
        ) from exc

    text_parts: list[str] = []
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            text_parts.append(page.get_text())

    return "\n".join(text_parts).strip()

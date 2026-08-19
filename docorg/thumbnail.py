from __future__ import annotations

from pathlib import Path

THUMBNAIL_WIDTH = 240


def thumbnail_path(doc_id: int, cfg: dict) -> Path:
    return Path(cfg["paths"]["documents"]) / ".thumbnails" / f"{doc_id}.jpg"


def generate_thumbnail(pdf_path: str | Path, dest_path: str | Path, *, max_width: int = THUMBNAIL_WIDTH) -> None:
    """Render the first page of *pdf_path* to a JPEG thumbnail at *dest_path*."""
    import fitz  # PyMuPDF

    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(str(pdf_path)) as doc:
        page = doc[0]
        zoom = max_width / page.rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(str(dest_path))


def ensure_thumbnail(doc_id: int, pdf_path: str | Path, cfg: dict) -> Path | None:
    """Return the thumbnail path for *doc_id*, generating it if missing.

    Returns None if generation fails (e.g. corrupt/empty PDF) rather than
    raising, since a missing thumbnail should never block filing or viewing.
    """
    dest = thumbnail_path(doc_id, cfg)
    if dest.exists():
        return dest
    try:
        generate_thumbnail(pdf_path, dest)
    except Exception:
        return None
    return dest

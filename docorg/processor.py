"""
Core processing pipeline — runs for every incoming PDF.
Called by both the file watcher (auto mode) and the CLI process command.
"""
import sqlite3
from pathlib import Path

from .database import document_exists, insert_document, update_filing
from .date_detector import detect_date
from .extractor import extract_text
from .filer import file_document


def _match_category(text: str, filename: str, rules: list[dict]) -> str | None:
    """
    Evaluate extracted text and filename against mapping rules.
    Returns the category of the highest-priority (lowest numeric) matching rule,
    or None if no rule matches (F21, F22, F23).
    """
    combined = f"{filename}\n{text}".lower()

    matched: list[dict] = []
    for rule in rules:
        for kw in rule.get("keywords", []):
            if kw.lower() in combined:
                matched.append(rule)
                break  # one keyword match is enough per rule

    if not matched:
        return None

    best = min(matched, key=lambda r: r.get("priority", 999))
    return best.get("category")


def process_pdf(
    pdf_path: str | Path,
    *,
    cfg: dict,
    conn: sqlite3.Connection,
) -> dict:
    """
    Full Phase-1 pipeline for a single PDF:
      1. Dedup check
      2. Text extraction
      3. Date detection
      4. Category detection
      5. Insert pending DB record
      6. File (move) to target folder
      7. Update DB record to 'filed'

    Returns a dict with processing results for display / TUI use.
    """
    pdf_path = Path(pdf_path)

    # NF3 — skip already-processed files
    if document_exists(conn, str(pdf_path)):
        return {"status": "duplicate", "path": str(pdf_path)}

    # Step 1: extract text
    text = extract_text(pdf_path)

    # Step 2: detect date
    configured_keywords = cfg.get("date_detection", {}).get("keywords")
    doc_date, candidate_count = detect_date(
        text,
        file_path=pdf_path,
        date_keywords=configured_keywords,
    )

    # Step 3: detect category
    rules: list[dict] = cfg.get("rules", [])
    category = _match_category(text, pdf_path.name, rules)
    classification_source = "rules" if (doc_date or category) else "fallback"

    # Step 4: insert pending record
    doc_id = insert_document(
        conn,
        filename=pdf_path.name,
        filepath=str(pdf_path),
        extracted_text=text,
        detected_date=doc_date.isoformat() if doc_date else None,
        category=category,
        classification_source=classification_source,
        filing_status="pending",
    )

    # Step 5: move file
    dest = file_document(
        pdf_path,
        documents_root=cfg["paths"]["documents"],
        doc_date=doc_date,
        category=category,
    )

    # Step 6: update DB with final path and filed status
    update_filing(conn, doc_id, filepath=str(dest), filing_status="filed")

    return {
        "status": "filed",
        "doc_id": doc_id,
        "filename": pdf_path.name,
        "dest": str(dest),
        "detected_date": doc_date.isoformat() if doc_date else None,
        "candidate_count": candidate_count,
        "category": category,
        "classification_source": classification_source,
    }

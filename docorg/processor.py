"""
Core processing pipeline — runs for every incoming PDF.
Called by both the file watcher (auto mode) and the CLI process command.
"""
import hashlib
import sqlite3
from pathlib import Path
from typing import Iterable

from .database import document_exists, document_exists_by_hash, insert_document, update_filing
from .date_detector import detect_date
from .extractor import extract_text
from .filer import file_document
from .pathing import to_stored_path


def _normalize_terms(terms: Iterable[str] | None) -> list[str]:
    if not terms:
        return []
    normalized: list[str] = []
    for term in terms:
        value = str(term).strip().lower()
        if value:
            normalized.append(value)
    return normalized


def _match_count(terms: Iterable[str], haystack: str) -> int:
    return sum(1 for term in terms if term and term in haystack)


def _weighted_hits(terms: Iterable[str], haystack: str) -> float:
    # Longer phrases are stronger signals than single words.
    score = 0.0
    for term in terms:
        if term and term in haystack:
            score += 1.0 + min(1.0, 0.25 * term.count(" "))
    return score


def _sha256_for_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _build_analysis(pdf_path: Path, *, cfg: dict) -> dict:
    text = extract_text(pdf_path)

    configured_keywords = cfg.get("date_detection", {}).get("keywords")
    doc_date, candidate_count = detect_date(
        text,
        file_path=pdf_path,
        date_keywords=configured_keywords,
    )

    rules: list[dict] = cfg.get("rules", [])
    classifier_cfg: dict = cfg.get("classification", {})
    category = _match_category(text, pdf_path.name, rules, classifier_cfg=classifier_cfg)
    classification_source = "rules" if (doc_date or category) else "fallback"

    return {
        "text": text,
        "doc_date": doc_date,
        "candidate_count": candidate_count,
        "category": category,
        "classification_source": classification_source,
    }


def analyze_pdf(pdf_path: str | Path, *, cfg: dict, conn: sqlite3.Connection) -> dict:
    pdf_path = Path(pdf_path)
    content_hash = _sha256_for_file(pdf_path)
    stored_source_path = to_stored_path(pdf_path, cfg)
    if (
        document_exists_by_hash(conn, content_hash)
        or document_exists(conn, stored_source_path)
        or document_exists(conn, str(pdf_path))
    ):
        return {"status": "duplicate", "path": str(pdf_path)}

    analysis = _build_analysis(pdf_path, cfg=cfg)
    return {
        "status": "analyzed",
        "filename": pdf_path.name,
        "path": str(pdf_path),
        "text": analysis["text"],
        "detected_date": analysis["doc_date"].isoformat() if analysis["doc_date"] else None,
        "candidate_count": analysis["candidate_count"],
        "category": analysis["category"],
        "classification_source": analysis["classification_source"],
    }


def _match_category(
    text: str,
    filename: str,
    rules: list[dict],
    *,
    classifier_cfg: dict | None = None,
) -> str | None:
    """
    Evaluate extracted text and filename against mapping rules.
    Returns the category of the highest-priority (lowest numeric) matching rule,
    or None if no rule matches (F21, F22, F23).
    """
    combined = f"{filename}\n{text}".lower()
    filename_lower = filename.lower()
    cfg = classifier_cfg or {}
    min_score = float(cfg.get("min_score", 1.0))
    min_score_gap = float(cfg.get("min_score_gap", 0.75))
    negative_weight = float(cfg.get("negative_weight", 1.25))

    category_scores: dict[str, float] = {}

    for rule in rules:
        category = rule.get("category")
        if not category:
            continue

        keywords = _normalize_terms(rule.get("keywords"))
        any_keywords = _normalize_terms(rule.get("any_keywords"))
        all_keywords = _normalize_terms(rule.get("all_keywords"))
        filename_keywords = _normalize_terms(rule.get("filename_keywords"))
        exclude_keywords = _normalize_terms(rule.get("exclude_keywords"))
        negative_keywords = _normalize_terms(rule.get("negative_keywords"))

        # Hard exclusions for known collisions.
        if _match_count(exclude_keywords, combined) > 0:
            continue

        # Support grouped rule semantics for higher precision.
        if any_keywords and _match_count(any_keywords, combined) == 0:
            continue
        if all_keywords and _match_count(all_keywords, combined) != len(all_keywords):
            continue

        positive_score = 0.0
        positive_score += _weighted_hits(keywords, combined)
        positive_score += _weighted_hits(any_keywords, combined)
        positive_score += _weighted_hits(all_keywords, combined)
        positive_score += 1.5 * _weighted_hits(filename_keywords, filename_lower)

        if positive_score <= 0:
            continue

        penalty = negative_weight * _match_count(negative_keywords, combined)
        rule_score = positive_score - penalty
        if rule_score <= 0:
            continue

        # Lower priority value is better; treat as a mild bonus, not a hard tie-break.
        priority = int(rule.get("priority", 999))
        priority_bonus = max(0.0, (100.0 - min(priority, 100)) / 100.0)
        rule_score += 0.2 * priority_bonus

        category_scores[category] = category_scores.get(category, 0.0) + rule_score

    if not category_scores:
        return None

    ranked = sorted(category_scores.items(), key=lambda item: (-item[1], item[0]))
    best_category, best_score = ranked[0]

    if best_score < min_score:
        return None

    if len(ranked) > 1:
        second_score = ranked[1][1]
        if (best_score - second_score) < min_score_gap:
            return None

    return best_category


def process_pdf(
    pdf_path: str | Path,
    *,
    cfg: dict,
    conn: sqlite3.Connection,
    override_date=None,
    override_category: str | None = None,
    override_source: str | None = None,
    override_ai_rationale: str | None = None,
    override_ai_summary: str | None = None,
    override_extracted_fields: dict[str, str] | None = None,
    skip: bool = False,
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
    content_hash = _sha256_for_file(pdf_path)
    file_size = pdf_path.stat().st_size
    stored_source_path = to_stored_path(pdf_path, cfg)

    # NF3 — skip already-processed files
    if (
        document_exists_by_hash(conn, content_hash)
        or document_exists(conn, stored_source_path)
        or document_exists(conn, str(pdf_path))
    ):
        return {"status": "duplicate", "path": str(pdf_path)}

    analysis = _build_analysis(pdf_path, cfg=cfg)
    text = analysis["text"]
    doc_date = analysis["doc_date"]
    candidate_count = analysis["candidate_count"]
    category = analysis["category"]
    classification_source = analysis["classification_source"]

    if override_date is not None:
        doc_date = override_date
    if override_category is not None:
        category = override_category
    if override_source is not None:
        classification_source = override_source
    ai_rationale = override_ai_rationale if override_source == "ai" else None
    ai_summary = override_ai_summary if override_source == "ai" else None
    extracted_fields = override_extracted_fields if override_source == "ai" else None
    ai_suggested_category = category if classification_source == "ai" else None

    # Step 4: insert pending record
    doc_id = insert_document(
        conn,
        filename=pdf_path.name,
        filepath=stored_source_path,
        content_hash=content_hash,
        file_size=file_size,
        extracted_text=text,
        detected_date=doc_date.isoformat() if doc_date else None,
        category=category,
        classification_source=classification_source,
        ai_suggested_category=ai_suggested_category,
        ai_rationale=ai_rationale,
        ai_summary=ai_summary,
        extracted_fields=extracted_fields,
        filing_status="pending",
        skipped=1 if skip else 0,
    )

    if skip:
        return {
            "status": "skipped",
            "doc_id": doc_id,
            "filename": pdf_path.name,
            "path": str(pdf_path),
            "detected_date": doc_date.isoformat() if doc_date else None,
            "candidate_count": candidate_count,
            "category": category,
            "classification_source": classification_source,
        }

    # Step 5: move file
    dest = file_document(
        pdf_path,
        documents_root=cfg["paths"]["documents"],
        doc_date=doc_date,
        category=category,
    )

    # Step 6: update DB with final path and filed status
    update_filing(conn, doc_id, filepath=to_stored_path(dest, cfg), filing_status="filed")

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

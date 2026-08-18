from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from datetime import timedelta
from urllib import request

from .settings import Settings
from .query_parser import parse_question, build_structured_query, execute_structured_query


@dataclass
class RetrievedDoc:
    id: int
    filename: str
    filepath: str
    detected_date: str | None
    category: str | None
    extracted_text: str
    score: float | None = None


def _build_retrieval_query(question: str) -> str:
    # Keep only useful alphanumeric tokens for FTS MATCH query.
    tokens = re.findall(r"[A-Za-z0-9]{2,}", question.lower())
    if not tokens:
        return "document"

    stopwords = {
        "the", "and", "for", "with", "from", "that", "this", "have", "has", "had",
        "into", "what", "when", "where", "which", "about", "give", "list", "last",
        "years", "year", "show", "tell", "all", "are", "was", "were", "can", "you",
        "do", "any", "i", "prior", "to",
    }
    filtered = [tok for tok in tokens if tok not in stopwords]
    # Also filter out bare year numbers (4-digit numbers) to avoid biasing retrieval
    # towards documents that mention that specific year in queries like "prior to 2022".
    filtered = [tok for tok in filtered if not (len(tok) == 4 and tok.isdigit())]
    terms = filtered if filtered else tokens

    # OR across terms to improve recall, cap to avoid huge queries.
    limited = terms[:12]
    return " OR ".join(limited)


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _looks_like_year_window_question(question: str) -> int | None:
    q = question.lower()
    m = re.search(r"last\s+(\d{1,2})\s+years?", q)
    if not m:
        return None
    years = int(m.group(1))
    if years < 1 or years > 50:
        return None
    return years


def _is_place_history_question(question: str) -> bool:
    q = question.lower()
    patterns = [
        r"places?\s+that\s+i\s+have\s+lived",
        r"where\s+have\s+i\s+lived",
        r"address(?:es)?\s+i\s+have\s+lived",
        r"residen(?:ce|tial)\s+history",
    ]
    return any(re.search(p, q) for p in patterns)


def _is_date_range_question(question: str) -> bool:
    """Detect questions about documents from a specific year/date range."""
    q = question.lower()
    patterns = [
        r"from\s+prior\s+to\s+\d{4}",
        r"before\s+\d{4}",
        r"prior\s+to\s+\d{4}",
        r"from\s+\d{4}",
        r"in\s+\d{4}",
        r"from\s+\d{4}\s+to\s+\d{4}",
    ]
    return any(re.search(p, q) for p in patterns)


def _retrieve_docs(db_path: str, retrieval_query: str, top_k: int) -> list[RetrievedDoc]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT d.id, d.filename, d.filepath, d.detected_date, d.category, d.extracted_text,
                   bm25(documents_fts) AS score
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (retrieval_query, top_k),
        ).fetchall()
    finally:
        conn.close()

    docs: list[RetrievedDoc] = []
    for row in rows:
        docs.append(
            RetrievedDoc(
                id=row["id"],
                filename=row["filename"],
                filepath=row["filepath"],
                detected_date=row["detected_date"],
                category=row["category"],
                extracted_text=row["extracted_text"] or "",
                score=float(row["score"]) if row["score"] is not None else None,
            )
        )
    return docs


def _filter_docs_for_year_window(docs: list[RetrievedDoc], years: int) -> list[RetrievedDoc]:
    cutoff = date.today() - timedelta(days=365 * years)
    filtered: list[RetrievedDoc] = []
    for doc in docs:
        d = _parse_iso_date(doc.detected_date)
        if d is None or d >= cutoff:
            filtered.append(doc)
    return filtered


def _build_context(docs: list[RetrievedDoc], per_doc_chars: int = 2200) -> str:
    blocks: list[str] = []
    for d in docs:
        text = (d.extracted_text or "").strip()
        excerpt = text[:per_doc_chars]
        blocks.append(
            "\n".join(
                [
                    f"[DOC {d.id}]",
                    f"filename: {d.filename}",
                    f"filepath: {d.filepath}",
                    f"detected_date: {d.detected_date or ''}",
                    f"category: {d.category or ''}",
                    "content:",
                    excerpt,
                ]
            )
        )
    return "\n\n".join(blocks)


def _build_prompt(question: str, context: str, place_history_mode: bool, date_range_mode: bool = False) -> str:
    if place_history_mode:
        return (
            "You are an assistant for personal document intelligence. "
            "Answer only from the provided context. If evidence is weak, say so clearly. "
            "For place-history questions, identify places lived and provide concise evidence with doc IDs. "
            "Do not invent addresses or dates.\n\n"
            f"Question:\n{question}\n\n"
            "Context:\n"
            f"{context}\n\n"
            "Output format:\n"
            "1) Bullet list of places lived\n"
            "2) Brief evidence line per place with [DOC id]\n"
            "3) Confidence note\n"
        )

    if date_range_mode:
        return (
            "You are an assistant for personal document intelligence. "
            "This question asks about documents from a specific date or date range. "
            "Carefully examine each document's detected_date field in the context. "
            "Answer YES or NO directly, then list the matching documents with their detected dates and filenames. "
            "Cite document IDs like [DOC 12]. Do not invent dates.\n\n"
            f"Question:\n{question}\n\n"
            "Context:\n"
            f"{context}\n\n"
            "Your response should start with YES or NO, followed by the evidence.\n"
        )

    return (
        "You are an assistant for personal document intelligence. "
        "Answer only from the provided context. "
        "If the context is insufficient, say exactly what is missing. "
        "Cite supporting document IDs in square brackets like [DOC 12].\n\n"
        f"Question:\n{question}\n\n"
        "Context:\n"
        f"{context}\n"
    )


def _call_ollama(ollama_url: str, model: str, prompt: str, timeout: int) -> str:
    url = ollama_url.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 900,
        },
    }
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return str(body.get("response", "")).strip()


def answer_question(
    question: str,
    settings: Settings,
    top_k_override: int | None = None,
) -> tuple[str, str, int, list[RetrievedDoc]]:
    db_path = settings.db_path
    ollama_url = settings.ollama_url
    model = settings.ollama_model
    timeout = settings.ollama_timeout
    default_top_k = settings.top_k
    top_k = top_k_override if top_k_override is not None else default_top_k

    # First, try structured parsing (regex, then LLM fallback)
    parsed = parse_question(
        question,
        db_path,
        ollama_url=ollama_url,
        ollama_model=model,
        ollama_timeout=timeout,
    )
    retrieval_query = ""
    docs: list[RetrievedDoc] = []

    if parsed.is_structured:
        # Use structured query for questions with multiple clear filters
        where_clause, params = build_structured_query(parsed, limit=top_k)
        rows = execute_structured_query(db_path, where_clause, params)
        docs = [
            RetrievedDoc(
                id=row["id"],
                filename=row["filename"],
                filepath=row["filepath"],
                detected_date=row["detected_date"],
                category=row["category"],
                extracted_text=row["extracted_text"] or "",
            )
            for row in rows
        ]
        retrieval_query = f"{'LLM-translated' if parsed.via_llm else 'Structured'} query: categories={parsed.categories}, date_from={parsed.date_from}, date_to={parsed.date_to}, names={parsed.names}"
    else:
        # Fall back to FTS for open-ended or unstructured questions
        retrieval_query = _build_retrieval_query(question)
        docs = _retrieve_docs(db_path, retrieval_query, top_k)

        year_window = _looks_like_year_window_question(question)
        place_history_mode = _is_place_history_question(question)
        date_range_mode = _is_date_range_question(question)
        
        if year_window and place_history_mode:
            docs = _filter_docs_for_year_window(docs, year_window)

    if not docs:
        return (
            "I could not find relevant documents matching those criteria.",
            retrieval_query,
            top_k,
            [],
        )

    context = _build_context(docs)
    
    # Adjust prompt based on whether we used structured or unstructured retrieval
    if parsed.is_structured:
        prompt = (
            "You are an assistant for personal document intelligence. "
            "The following documents were retrieved using structured filters (category, date range, names). "
            "Format your answer clearly, listing each matching document with its date and category. "
            "Cite document IDs like [DOC 12].\n\n"
            f"Question:\n{question}\n\n"
            "Documents found:\n"
            f"{context}\n"
        )
    else:
        place_history_mode = _is_place_history_question(question)
        date_range_mode = _is_date_range_question(question)
        prompt = _build_prompt(question, context, place_history_mode, date_range_mode)

    try:
        answer = _call_ollama(ollama_url, model, prompt, timeout)
    except Exception as exc:
        answer = f"Model call failed: {exc}"

    return answer, retrieval_query, top_k, docs

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from datetime import timedelta
from urllib import request


@dataclass
class RetrievedDoc:
    id: int
    filename: str
    filepath: str
    detected_date: str | None
    category: str | None
    extracted_text: str
    score: float | None = None


def get_env(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    return value if value else default


def _build_retrieval_query(question: str) -> str:
    # Keep only useful alphanumeric tokens for FTS MATCH query.
    tokens = re.findall(r"[A-Za-z0-9]{2,}", question.lower())
    if not tokens:
        return "document"

    stopwords = {
        "the", "and", "for", "with", "from", "that", "this", "have", "has", "had",
        "into", "what", "when", "where", "which", "about", "give", "list", "last",
        "years", "year", "show", "tell", "all", "are", "was", "were", "can", "you",
    }
    filtered = [tok for tok in tokens if tok not in stopwords]
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


def _build_prompt(question: str, context: str, place_history_mode: bool) -> str:
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


def answer_question(question: str, top_k_override: int | None = None) -> tuple[str, str, int, list[RetrievedDoc]]:
    db_path = get_env("DOCORG_DB_PATH", "/data/docorganizer.db")
    ollama_url = get_env("OLLAMA_URL", "http://ollama:11434")
    model = get_env("OLLAMA_MODEL", "mistral:7b-instruct")
    timeout = int(get_env("OLLAMA_TIMEOUT", "180"))
    default_top_k = int(get_env("TOP_K", "8"))
    top_k = top_k_override if top_k_override is not None else default_top_k

    retrieval_query = _build_retrieval_query(question)
    docs = _retrieve_docs(db_path, retrieval_query, top_k)

    year_window = _looks_like_year_window_question(question)
    place_history_mode = _is_place_history_question(question)
    if year_window and place_history_mode:
        docs = _filter_docs_for_year_window(docs, year_window)

    if not docs:
        return (
            "I could not find relevant documents in the index for this question.",
            retrieval_query,
            top_k,
            [],
        )

    context = _build_context(docs)
    prompt = _build_prompt(question, context, place_history_mode)

    try:
        answer = _call_ollama(ollama_url, model, prompt, timeout)
    except Exception as exc:
        answer = f"Model call failed: {exc}"

    return answer, retrieval_query, top_k, docs

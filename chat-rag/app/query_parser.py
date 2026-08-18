from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from urllib import request as urllib_request


@dataclass
class ParsedQuery:
    """Result of parsing a natural language question into structured filters."""
    categories: list[str]
    date_from: date | None
    date_to: date | None
    names: list[str]
    keywords: list[str]
    is_structured: bool  # True if LLM extracted at least one usable filter
    via_llm: bool = False  # True if LLM translation was used


def _fetch_known_categories(db_path: str) -> list[str]:
    """Return distinct category values from the database."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT category FROM documents WHERE category IS NOT NULL ORDER BY category"
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def _extract_keywords(question: str) -> list[str]:
    """Extract significant keywords for fallback FTS search."""
    q = question.lower()
    tokens = re.findall(r"[A-Za-z0-9]{2,}", q)

    stopwords = {
        "the", "and", "for", "with", "from", "that", "this", "have", "has", "had",
        "into", "what", "when", "where", "which", "about", "give", "list", "last",
        "years", "year", "show", "tell", "all", "are", "was", "were", "can", "you",
        "do", "any", "i", "prior", "to", "we", "our", "related", "docs", "documents",
        "document", "month", "months", "day", "days", "week", "weeks",
    }
    filtered = [t for t in tokens if t not in stopwords and not (len(t) == 4 and t.isdigit())]
    return filtered[:10]


def translate_to_filters_via_llm(
    question: str,
    db_path: str,
    ollama_url: str,
    ollama_model: str,
    ollama_timeout: int,
) -> ParsedQuery | None:
    """Ask the LLM to extract structured filters from a natural-language question.

    Returns a ParsedQuery with is_structured=True if at least one filter was found,
    or None if the LLM response is unparseable or yields no usable filters.
    """
    today = date.today()
    known_categories = _fetch_known_categories(db_path)
    categories_str = ", ".join(known_categories) if known_categories else "(none)"

    prompt = (
        "You are a query-extraction assistant. "
        "Your ONLY job is to extract structured filter fields from the user question. "
        "Respond with a single JSON object — no prose, no explanation, just the JSON.\n\n"
        "JSON schema (all fields optional, omit what you cannot determine):\n"
        "{\n"
        '  "categories": ["<exact category name from the allowed list>"],\n'
        '  "date_from": "<YYYY-MM-DD>",\n'
        '  "date_to": "<YYYY-MM-DD>",\n'
        '  "names": ["<person name>"],\n'
        '  "keywords": ["<important word>"]\n'
        "}\n\n"
        f"Today's date: {today.isoformat()}\n"
        f"Allowed category values: {categories_str}\n\n"
        "Rules:\n"
        "- Only use category values from the allowed list above (case-sensitive).\n"
        "- Relative dates like 'last 3 months' → compute absolute dates from today.\n"
        "- 'last year' → date_from = Jan 1 of previous year, date_to = Dec 31 of previous year.\n"
        "- 'this year' → date_from = Jan 1 of current year, date_to = today.\n"
        "- If a date bound is open-ended, omit that field entirely.\n"
        "- names: only person names, not places or companies.\n"
        "- keywords: significant words not already covered by category or names.\n"
        "- Omit any field you cannot determine — do not output null values.\n\n"
        f"Question: {question}"
    )

    payload = {
        "model": ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 300,
        },
    }

    url = ollama_url.rstrip("/") + "/api/generate"
    req = urllib_request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=ollama_timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        raw_text = str(body.get("response", "")).strip()
    except Exception:
        return None

    # Extract JSON from the response (model may wrap it in markdown fences)
    json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not json_match:
        return None

    try:
        data = json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    # Parse categories — validate against known list
    raw_cats = data.get("categories") or []
    known_lower = {c.lower(): c for c in known_categories}
    categories: list[str] = []
    for c in raw_cats:
        if isinstance(c, str):
            canonical = known_lower.get(c.lower())
            if canonical:
                categories.append(canonical)

    # Parse dates
    def _parse_date(val: object) -> date | None:
        if not isinstance(val, str):
            return None
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", val.strip())
        if not m:
            return None
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    date_from = _parse_date(data.get("date_from"))
    date_to = _parse_date(data.get("date_to"))

    # Parse names
    raw_names = data.get("names") or []
    names = [n for n in raw_names if isinstance(n, str) and len(n) > 1]

    # Parse keywords
    raw_kw = data.get("keywords") or []
    keywords = [k for k in raw_kw if isinstance(k, str)][:10]

    has_filters = bool(categories or date_from or date_to or names)
    if not has_filters:
        return None  # LLM found nothing actionable — caller falls back to FTS

    return ParsedQuery(
        categories=categories,
        date_from=date_from,
        date_to=date_to,
        names=names,
        keywords=keywords,
        is_structured=True,
        via_llm=True,
    )


def parse_question(
    question: str,
    db_path: str,
    ollama_url: str | None = None,
    ollama_model: str | None = None,
    ollama_timeout: int = 60,
) -> ParsedQuery:
    """Parse a natural language question into structured filters.

    Uses LLM translation as the primary path when Ollama details are provided.
    Falls back to keyword-only FTS if Ollama is unavailable or returns nothing.
    """
    keywords = _extract_keywords(question)

    if ollama_url and ollama_model:
        llm_result = translate_to_filters_via_llm(
            question, db_path, ollama_url, ollama_model, ollama_timeout
        )
        if llm_result is not None:
            if not llm_result.keywords:
                llm_result.keywords = keywords
            return llm_result

    # No Ollama or LLM returned nothing — FTS fallback
    return ParsedQuery(
        categories=[],
        date_from=None,
        date_to=None,
        names=[],
        keywords=keywords,
        is_structured=False,
        via_llm=False,
    )


def build_structured_query(parsed: ParsedQuery, limit: int = 12) -> tuple[str, tuple]:
    """Build a SQL WHERE clause and params from parsed query.
    
    Returns (where_clause, params) to be used with:
        SELECT d.* FROM documents d WHERE {where_clause} LIMIT ?
    """
    conditions: list[str] = []
    params: list[object] = []

    if parsed.categories:
        placeholders = ", ".join(["?" for _ in parsed.categories])
        conditions.append(f"d.category IN ({placeholders})")
        params.extend(parsed.categories)

    if parsed.date_from:
        conditions.append("d.detected_date >= ?")
        params.append(parsed.date_from.isoformat())

    if parsed.date_to:
        conditions.append("d.detected_date <= ?")
        params.append(parsed.date_to.isoformat())

    if parsed.names:
        # Search for names in extracted_text
        name_conditions = " OR ".join([f"d.extracted_text LIKE ?" for _ in parsed.names])
        conditions.append(f"({name_conditions})")
        params.extend([f"%{name}%" for name in parsed.names])

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    return where_clause, tuple(params)


def execute_structured_query(db_path: str, where_clause: str, params: tuple) -> list[sqlite3.Row]:
    """Execute a structured query and return results."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT d.id, d.filename, d.filepath, d.detected_date, d.category, d.extracted_text
            FROM documents d
            WHERE {where_clause}
            ORDER BY d.detected_date DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return list(rows)
    finally:
        conn.close()

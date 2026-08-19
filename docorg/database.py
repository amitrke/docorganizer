import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS documents (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    filename             TEXT NOT NULL,
    filepath             TEXT NOT NULL UNIQUE,
    content_hash         TEXT,
    file_size            INTEGER,
    extracted_text       TEXT,
    detected_date        TEXT,
    category             TEXT,
    ai_suggested_category TEXT,
    classification_source TEXT NOT NULL DEFAULT 'rules',
    ai_rationale         TEXT,
    ai_summary           TEXT,
    extracted_fields     TEXT,
    primary_person       TEXT,
    issuing_organization TEXT,
    reference_number     TEXT,
    amount               TEXT,
    effective_date       TEXT,
    expiry_date          TEXT,
    archived_at          TEXT,
    archived_reason      TEXT,
    filing_status        TEXT NOT NULL DEFAULT 'pending',
    last_reviewed_at     TEXT,
    skipped              INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
"""

# FTS5 virtual table (content table mirrors documents) + sync triggers. Kept as its
# own script so `_migrate()` can drop and reapply it verbatim when upgrading an
# existing DB whose FTS schema predates the primary_person/issuing_organization/
# reference_number columns — FTS5 virtual tables can't be ALTERed to add columns.
_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    filename,
    extracted_text,
    primary_person,
    issuing_organization,
    reference_number,
    content='documents',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, filename, extracted_text, primary_person, issuing_organization, reference_number)
    VALUES (new.id, new.filename, new.extracted_text, new.primary_person, new.issuing_organization, new.reference_number);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, filename, extracted_text, primary_person, issuing_organization, reference_number)
    VALUES ('delete', old.id, old.filename, old.extracted_text, old.primary_person, old.issuing_organization, old.reference_number);
    INSERT INTO documents_fts(rowid, filename, extracted_text, primary_person, issuing_organization, reference_number)
    VALUES (new.id, new.filename, new.extracted_text, new.primary_person, new.issuing_organization, new.reference_number);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, filename, extracted_text, primary_person, issuing_organization, reference_number)
    VALUES ('delete', old.id, old.filename, old.extracted_text, old.primary_person, old.issuing_organization, old.reference_number);
END;
"""

DDL = _TABLES_DDL + _FTS_DDL + """
-- Key/value store for web-configured settings (legacy single AI config; kept
-- only as a migration source for ai_profiles below).
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Named, switchable AI provider configs. At most one row has is_active=1;
-- enforced in application code (set_active_ai_profile), not a DB constraint.
CREATE TABLE IF NOT EXISTS ai_profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    provider    TEXT NOT NULL DEFAULT 'ollama',
    model       TEXT NOT NULL DEFAULT '',
    base_url    TEXT NOT NULL DEFAULT '',
    api_key     TEXT NOT NULL DEFAULT '',
    timeout     INTEGER NOT NULL DEFAULT 180,
    max_tokens  INTEGER NOT NULL DEFAULT 1200,
    is_active   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def serialize_extracted_fields(fields: dict[str, str] | None) -> str | None:
    if not fields:
        return None
    cleaned = {
        str(key): str(value)
        for key, value in fields.items()
        if str(key).strip() and str(value).strip()
    }
    if not cleaned:
        return None
    return json.dumps(cleaned, sort_keys=True)


def parse_extracted_fields(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in parsed.items()
        if str(key).strip() and value is not None and str(value).strip()
    }


def _migrate(conn) -> None:
    """Apply incremental schema changes to existing databases."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "content_hash" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT")
    if "file_size" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN file_size INTEGER")
    if "ai_rationale" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN ai_rationale TEXT")
    if "ai_summary" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN ai_summary TEXT")
    if "extracted_fields" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN extracted_fields TEXT")
    if "ai_suggested_category" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN ai_suggested_category TEXT")
    if "primary_person" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN primary_person TEXT")
    if "issuing_organization" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN issuing_organization TEXT")
    if "reference_number" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN reference_number TEXT")
    if "amount" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN amount TEXT")
    if "effective_date" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN effective_date TEXT")
    if "expiry_date" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN expiry_date TEXT")
    if "archived_at" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN archived_at TEXT")
    if "archived_reason" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN archived_reason TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_expiry_date ON documents(expiry_date)")

    # FTS5 virtual tables can't be ALTERed to add columns — if this DB's index predates
    # primary_person/issuing_organization/reference_number, drop and rebuild it.
    fts_row = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'documents_fts'").fetchone()
    if fts_row and "primary_person" not in (fts_row[0] or ""):
        conn.execute("DROP TRIGGER IF EXISTS documents_ai")
        conn.execute("DROP TRIGGER IF EXISTS documents_au")
        conn.execute("DROP TRIGGER IF EXISTS documents_ad")
        conn.execute("DROP TABLE IF EXISTS documents_fts")
        conn.executescript(_FTS_DDL)
        conn.execute("INSERT INTO documents_fts(documents_fts) VALUES ('rebuild')")

    conn.commit()


def init_db(db_path: str | Path) -> None:
    """Create tables and triggers if they do not already exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(DDL)
        _migrate(conn)


def insert_document(conn: sqlite3.Connection, *, filename: str, filepath: str,
                    content_hash: str | None = None,
                    file_size: int | None = None,
                    extracted_text: str, detected_date: str | None,
                    category: str | None, classification_source: str = "rules",
                    ai_suggested_category: str | None = None,
                    ai_rationale: str | None = None,
                    ai_summary: str | None = None,
                    extracted_fields: dict[str, str] | None = None,
                    primary_person: str | None = None,
                    issuing_organization: str | None = None,
                    reference_number: str | None = None,
                    amount: str | None = None,
                    effective_date: str | None = None,
                    expiry_date: str | None = None,
                    filing_status: str = "pending", skipped: int = 0) -> int:
    cur = conn.execute(
        """
        INSERT INTO documents
            (filename, filepath, content_hash, file_size, extracted_text, detected_date,
               category, ai_suggested_category, classification_source, ai_rationale, ai_summary,
               extracted_fields, primary_person, issuing_organization, reference_number, amount,
               effective_date, expiry_date, filing_status, skipped)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (filename, filepath, content_hash, file_size, extracted_text, detected_date,
            category, ai_suggested_category, classification_source, ai_rationale, ai_summary,
            serialize_extracted_fields(extracted_fields), primary_person, issuing_organization,
            reference_number, amount, effective_date, expiry_date, filing_status, skipped),
    )
    conn.commit()
    return cur.lastrowid


def document_exists(conn: sqlite3.Connection, filepath: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM documents WHERE filepath = ?", (filepath,)
    ).fetchone()
    return row is not None


def document_exists_by_hash(conn: sqlite3.Connection, content_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM documents WHERE content_hash = ? LIMIT 1", (content_hash,)
    ).fetchone()
    return row is not None


def update_filing(conn: sqlite3.Connection, doc_id: int, *,
                  filepath: str, filing_status: str = "filed") -> None:
    conn.execute(
        """
        UPDATE documents
        SET filepath = ?, filing_status = ?
        WHERE id = ?
        """,
        (filepath, filing_status, doc_id),
    )
    conn.commit()


def _like_pattern(value: str) -> str:
    """Build a case-insensitive substring LIKE pattern, escaping % and _ so they
    match literally rather than as LIKE wildcards."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _sanitize_fts_query(query: str) -> str:
    """Quote each whitespace-separated token so FTS5 query-syntax characters
    (-, ", *, :, (, ) etc. — common in reference numbers) are treated as
    literal text to match rather than causing a MATCH syntax error."""
    return " ".join('"' + token.replace('"', '""') + '"' for token in query.split())


def search_documents(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    sanitized = _sanitize_fts_query(query)
    if not sanitized:
        return []
    return conn.execute(
        """
        SELECT d.*
        FROM documents d
        JOIN documents_fts f ON d.id = f.rowid
        WHERE documents_fts MATCH ?
        ORDER BY rank
        """,
        (sanitized,),
    ).fetchall()


#: Whitelisted for interpolation into ORDER BY — never build this from unchecked user input.
SORT_COLUMNS = {"id", "filename", "detected_date", "category", "classification_source", "filing_status", "created_at"}


def list_documents(conn: sqlite3.Connection, *, status: str = "all",
                   category: str | None = None,
                   date_from: str | None = None, date_to: str | None = None,
                   expiry_from: str | None = None, expiry_to: str | None = None,
                   amount_min: float | None = None, amount_max: float | None = None,
                   person: str | None = None, organization: str | None = None,
                   reference_number: str | None = None,
                   sort_by: str = "detected_date", sort_dir: str = "desc",
                   archived_only: bool = False) -> list[sqlite3.Row]:
    where_parts: list[str] = []
    params: list[object] = []

    where_parts.append("archived_at IS NOT NULL" if archived_only else "archived_at IS NULL")

    if status in {"pending", "filed"}:
        where_parts.append("filing_status = ?")
        params.append(status)

    if category:
        where_parts.append("category = ?")
        params.append(category)

    if person:
        where_parts.append("primary_person LIKE ? ESCAPE '\\' COLLATE NOCASE")
        params.append(_like_pattern(person))

    if organization:
        where_parts.append("issuing_organization LIKE ? ESCAPE '\\' COLLATE NOCASE")
        params.append(_like_pattern(organization))

    if reference_number:
        where_parts.append("reference_number LIKE ? ESCAPE '\\' COLLATE NOCASE")
        params.append(_like_pattern(reference_number))

    if date_from:
        where_parts.append("detected_date >= ?")
        params.append(date_from)

    if date_to:
        where_parts.append("detected_date <= ?")
        params.append(date_to)

    if expiry_from:
        where_parts.append("expiry_date >= ?")
        params.append(expiry_from)

    if expiry_to:
        where_parts.append("expiry_date <= ?")
        params.append(expiry_to)

    if amount_min is not None:
        where_parts.append("CAST(amount AS REAL) >= ?")
        params.append(amount_min)

    if amount_max is not None:
        where_parts.append("CAST(amount AS REAL) <= ?")
        params.append(amount_max)

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    sort_column = sort_by if sort_by in SORT_COLUMNS else "detected_date"
    direction = "ASC" if sort_dir == "asc" else "DESC"
    # `(col IS NULL)` ascending keeps rows with no value last regardless of direction.
    return conn.execute(
        f"""
        SELECT *
        FROM documents
        {where_sql}
        ORDER BY ({sort_column} IS NULL), {sort_column} {direction}, id DESC
        """,
        params,
    ).fetchall()


def archive_document(conn: sqlite3.Connection, doc_id: int, reason: str) -> None:
    update_document_fields(
        conn, doc_id,
        archived_at=_now_iso(), archived_reason=reason,
        touch_reviewed_at=False,
    )


def unarchive_document(conn: sqlite3.Connection, doc_id: int) -> None:
    update_document_fields(conn, doc_id, clear_archived=True, touch_reviewed_at=False)


def sweep_expired_documents(conn: sqlite3.Connection) -> int:
    """Auto-archive filed documents whose expiry_date has passed. Returns rows affected."""
    cur = conn.execute(
        """
        UPDATE documents
        SET archived_at = ?, archived_reason = 'expired'
        WHERE archived_at IS NULL
          AND expiry_date IS NOT NULL
          AND expiry_date < date('now')
          AND filing_status = 'filed'
        """,
        (_now_iso(),),
    )
    conn.commit()
    return cur.rowcount


def get_document_by_id(conn: sqlite3.Connection, doc_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()


def get_app_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_app_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()


def list_ai_profiles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ai_profiles ORDER BY created_at, id"
    ).fetchall()


def get_ai_profile(conn: sqlite3.Connection, profile_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM ai_profiles WHERE id = ?", (profile_id,)
    ).fetchone()


def get_active_ai_profile(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM ai_profiles WHERE is_active = 1 LIMIT 1"
    ).fetchone()


def insert_ai_profile(conn: sqlite3.Connection, *, name: str, provider: str, model: str,
                      base_url: str, api_key: str, timeout: int, max_tokens: int,
                      is_active: bool = False) -> int:
    if is_active:
        conn.execute("UPDATE ai_profiles SET is_active = 0")
    cur = conn.execute(
        """
        INSERT INTO ai_profiles (name, provider, model, base_url, api_key, timeout, max_tokens, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, provider, model, base_url, api_key, timeout, max_tokens, 1 if is_active else 0),
    )
    conn.commit()
    return cur.lastrowid


def update_ai_profile(conn: sqlite3.Connection, profile_id: int, *,
                      name: str | None = None, provider: str | None = None,
                      model: str | None = None, base_url: str | None = None,
                      api_key: str | None = None, timeout: int | None = None,
                      max_tokens: int | None = None) -> None:
    updates: list[str] = []
    params: list[object] = []
    for column, value in (
        ("name", name), ("provider", provider), ("model", model), ("base_url", base_url),
        ("api_key", api_key), ("timeout", timeout), ("max_tokens", max_tokens),
    ):
        if value is not None:
            updates.append(f"{column} = ?")
            params.append(value)
    if not updates:
        return
    params.append(profile_id)
    conn.execute(f"UPDATE ai_profiles SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()


def delete_ai_profile(conn: sqlite3.Connection, profile_id: int) -> None:
    conn.execute("DELETE FROM ai_profiles WHERE id = ?", (profile_id,))
    conn.commit()


def set_active_ai_profile(conn: sqlite3.Connection, profile_id: int) -> None:
    conn.execute("UPDATE ai_profiles SET is_active = 0")
    conn.execute("UPDATE ai_profiles SET is_active = 1 WHERE id = ?", (profile_id,))
    conn.commit()


def clear_active_ai_profile(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE ai_profiles SET is_active = 0")
    conn.commit()


def list_categories(conn: sqlite3.Connection) -> list[str]:
    """Return all distinct non-null category values stored in the database."""
    rows = conn.execute(
        "SELECT DISTINCT category FROM documents WHERE category IS NOT NULL ORDER BY category"
    ).fetchall()
    return [row[0] for row in rows]


def update_document_fields(conn: sqlite3.Connection, doc_id: int, *,
                           detected_date: str | None = None,
                           category: str | None = None,
                           ai_suggested_category: str | None = None,
                           classification_source: str | None = None,
                           ai_rationale: str | None = None,
                           ai_summary: str | None = None,
                           extracted_fields: dict[str, str] | None = None,
                           primary_person: str | None = None,
                           issuing_organization: str | None = None,
                           reference_number: str | None = None,
                           amount: str | None = None,
                           effective_date: str | None = None,
                           expiry_date: str | None = None,
                           filepath: str | None = None,
                           filing_status: str | None = None,
                           skipped: int | None = None,
                           archived_at: str | None = None,
                           archived_reason: str | None = None,
                           clear_ai_metadata: bool = False,
                           clear_category: bool = False,
                           clear_archived: bool = False,
                           touch_reviewed_at: bool = True) -> None:
    updates: list[str] = []
    params: list[object] = []

    if detected_date is not None:
        updates.append("detected_date = ?")
        params.append(detected_date)
    if clear_category:
        updates.append("category = NULL")
    elif category is not None:
        updates.append("category = ?")
        params.append(category)
    if ai_suggested_category is not None:
        updates.append("ai_suggested_category = ?")
        params.append(ai_suggested_category)
    if classification_source is not None:
        updates.append("classification_source = ?")
        params.append(classification_source)
    if ai_rationale is not None:
        updates.append("ai_rationale = ?")
        params.append(ai_rationale)
    if ai_summary is not None:
        updates.append("ai_summary = ?")
        params.append(ai_summary)
    if extracted_fields is not None:
        updates.append("extracted_fields = ?")
        params.append(serialize_extracted_fields(extracted_fields))
    if primary_person is not None:
        updates.append("primary_person = ?")
        params.append(primary_person)
    if issuing_organization is not None:
        updates.append("issuing_organization = ?")
        params.append(issuing_organization)
    if reference_number is not None:
        updates.append("reference_number = ?")
        params.append(reference_number)
    if amount is not None:
        updates.append("amount = ?")
        params.append(amount)
    if effective_date is not None:
        updates.append("effective_date = ?")
        params.append(effective_date)
    if expiry_date is not None:
        updates.append("expiry_date = ?")
        params.append(expiry_date)
    if filepath is not None:
        updates.append("filepath = ?")
        params.append(filepath)
    if filing_status is not None:
        updates.append("filing_status = ?")
        params.append(filing_status)
    if skipped is not None:
        updates.append("skipped = ?")
        params.append(skipped)
    if clear_archived:
        updates.append("archived_at = NULL")
        updates.append("archived_reason = NULL")
    else:
        if archived_at is not None:
            updates.append("archived_at = ?")
            params.append(archived_at)
        if archived_reason is not None:
            updates.append("archived_reason = ?")
            params.append(archived_reason)
    if clear_ai_metadata:
        updates.append("ai_suggested_category = NULL")
        updates.append("ai_rationale = NULL")
        updates.append("ai_summary = NULL")
        updates.append("extracted_fields = NULL")
    if touch_reviewed_at:
        updates.append("last_reviewed_at = strftime('%Y-%m-%dT%H:%M:%S', 'now')")

    if not updates:
        return

    params.append(doc_id)
    conn.execute(
        f"UPDATE documents SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()

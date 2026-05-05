import sqlite3
from pathlib import Path


DDL = """
CREATE TABLE IF NOT EXISTS documents (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    filename             TEXT NOT NULL,
    filepath             TEXT NOT NULL UNIQUE,
    extracted_text       TEXT,
    detected_date        TEXT,
    category             TEXT,
    classification_source TEXT NOT NULL DEFAULT 'rules',
    filing_status        TEXT NOT NULL DEFAULT 'pending',
    last_reviewed_at     TEXT,
    skipped              INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

-- FTS5 virtual table (content table mirrors documents)
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    filename,
    extracted_text,
    content='documents',
    content_rowid='id'
);

-- Keep FTS in sync with the base table
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, filename, extracted_text)
    VALUES (new.id, new.filename, new.extracted_text);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, filename, extracted_text)
    VALUES ('delete', old.id, old.filename, old.extracted_text);
    INSERT INTO documents_fts(rowid, filename, extracted_text)
    VALUES (new.id, new.filename, new.extracted_text);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, filename, extracted_text)
    VALUES ('delete', old.id, old.filename, old.extracted_text);
END;
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path) -> None:
    """Create tables and triggers if they do not already exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(DDL)


def insert_document(conn: sqlite3.Connection, *, filename: str, filepath: str,
                    extracted_text: str, detected_date: str | None,
                    category: str | None, classification_source: str = "rules",
                    filing_status: str = "pending", skipped: int = 0) -> int:
    cur = conn.execute(
        """
        INSERT INTO documents
            (filename, filepath, extracted_text, detected_date,
               category, classification_source, filing_status, skipped)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (filename, filepath, extracted_text, detected_date,
            category, classification_source, filing_status, skipped),
    )
    conn.commit()
    return cur.lastrowid


def document_exists(conn: sqlite3.Connection, filepath: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM documents WHERE filepath = ?", (filepath,)
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


def search_documents(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT d.*
        FROM documents d
        JOIN documents_fts f ON d.id = f.rowid
        WHERE documents_fts MATCH ?
        ORDER BY rank
        """,
        (query,),
    ).fetchall()


def list_documents(conn: sqlite3.Connection, *, status: str = "all",
                   category: str | None = None) -> list[sqlite3.Row]:
    where_parts: list[str] = []
    params: list[str] = []

    if status in {"pending", "filed"}:
        where_parts.append("filing_status = ?")
        params.append(status)

    if category:
        where_parts.append("category = ?")
        params.append(category)

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    return conn.execute(
        f"""
        SELECT *
        FROM documents
        {where_sql}
        ORDER BY skipped DESC, created_at DESC, id DESC
        """,
        params,
    ).fetchall()


def get_document_by_id(conn: sqlite3.Connection, doc_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()


def update_document_fields(conn: sqlite3.Connection, doc_id: int, *,
                           detected_date: str | None = None,
                           category: str | None = None,
                           classification_source: str | None = None,
                           filepath: str | None = None,
                           filing_status: str | None = None,
                           skipped: int | None = None,
                           touch_reviewed_at: bool = True) -> None:
    updates: list[str] = []
    params: list[object] = []

    if detected_date is not None:
        updates.append("detected_date = ?")
        params.append(detected_date)
    if category is not None:
        updates.append("category = ?")
        params.append(category)
    if classification_source is not None:
        updates.append("classification_source = ?")
        params.append(classification_source)
    if filepath is not None:
        updates.append("filepath = ?")
        params.append(filepath)
    if filing_status is not None:
        updates.append("filing_status = ?")
        params.append(filing_status)
    if skipped is not None:
        updates.append("skipped = ?")
        params.append(skipped)
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

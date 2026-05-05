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
                    filing_status: str = "pending") -> int:
    cur = conn.execute(
        """
        INSERT INTO documents
            (filename, filepath, extracted_text, detected_date,
             category, classification_source, filing_status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (filename, filepath, extracted_text, detected_date,
         category, classification_source, filing_status),
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

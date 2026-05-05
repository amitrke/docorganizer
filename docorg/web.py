from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from .database import get_connection, get_document_by_id, list_documents, parse_extracted_fields, search_documents
from .pathing import resolve_stored_path


def _fmt(value: object | None, default: str = "(none)") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _resolve_doc_path(filepath: str) -> Path:
    doc_path = Path(filepath)
    if not doc_path.is_absolute():
        doc_path = Path.cwd() / doc_path
    return doc_path.resolve()


def _apply_path_rewrites(filepath: str, rewrites: list[dict]) -> str:
    """Apply configured path prefix rewrites (e.g. Windows share path → container path).

    Rewrites are applied in order; the first matching rule wins.
    Comparison is case-insensitive to handle Windows drive-letter casing.
    """
    for rule in rewrites:
        src = rule.get("from", "")
        dst = rule.get("to", "")
        if not src:
            continue
        norm_filepath = filepath.replace("\\", "/")
        norm_src = src.replace("\\", "/")
        if norm_filepath.lower().startswith(norm_src.lower()):
            remainder = filepath[len(src):].lstrip("/\\")
            return dst.rstrip("/") + "/" + remainder if remainder else dst.rstrip("/")
    return filepath


def _translate_filepath(filepath: str, base_from: str | None, base_to: str | None, rewrites: list[dict]) -> str:
    """Translate stored file paths for the web host.

    Priority:
    1) Single base mapping (`base_from` -> `base_to`) for common split-host setups.
    2) Legacy/advanced list mapping via `path_rewrite`.
    """
    if base_from and base_to:
        norm_filepath = filepath.replace("\\", "/")
        norm_from = base_from.replace("\\", "/")
        if norm_filepath.lower().startswith(norm_from.lower()):
            remainder = filepath[len(base_from):].lstrip("/\\")
            return base_to.rstrip("/") + "/" + remainder if remainder else base_to.rstrip("/")

    return _apply_path_rewrites(filepath, rewrites)


def _resolve_db_filepath(filepath: str, cfg: dict, base_from: str | None,
                         base_to: str | None, rewrites: list[dict]) -> Path:
    """Resolve DB filepath supporting both host-neutral and legacy absolute records."""
    normalized = filepath.replace("\\", "/")
    if normalized.startswith("documents/") or normalized.startswith("inbox/"):
        return resolve_stored_path(filepath, cfg)

    translated = _translate_filepath(filepath, base_from, base_to, rewrites)
    return _resolve_doc_path(translated)


def _render_home(cfg: dict, query: str, status: str, category: str | None, rows: list) -> str:
    status_options = ["all", "pending", "filed"]
    categories = cfg.get("categories", [])

    def _status_option(value: str) -> str:
        selected = " selected" if value == status else ""
        return f'<option value="{escape(value)}"{selected}>{escape(value.title())}</option>'

    category_options = ['<option value="">All categories</option>']
    for cat in categories:
        selected = " selected" if category == cat else ""
        category_options.append(f'<option value="{escape(cat)}"{selected}>{escape(cat)}</option>')

    row_html: list[str] = []
    if not rows:
        row_html.append(
            "<tr><td colspan=\"7\"><div class=\"empty\">No documents match this filter.</div></td></tr>"
        )
    else:
        for row in rows:
            row_html.append(
                """
                <tr>
                    <td class="mono">{id}</td>
                    <td>{filename}</td>
                    <td>{detected_date}</td>
                    <td>{category}</td>
                    <td>{source}</td>
                    <td>{status}</td>
                    <td class="actions">
                        <a class="btn subtle" href="/documents/{id}">Details</a>
                        <a class="btn" href="/documents/{id}/content" target="_blank" rel="noopener noreferrer">View</a>
                    </td>
                </tr>
                """.format(
                    id=row["id"],
                    filename=escape(_fmt(row["filename"])),
                    detected_date=escape(_fmt(row["detected_date"])),
                    category=escape(_fmt(row["category"])),
                    source=escape(_fmt(row["classification_source"])),
                    status=escape(_fmt(row["filing_status"])),
                )
            )

    return f"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>docorg browser</title>
    <style>
        :root {{
            --ink: #1f1d1b;
            --paper: #f8f4eb;
            --paper-soft: #f2ece0;
            --accent: #1f6f6d;
            --accent-soft: #dcefeb;
            --warm: #b7602a;
            --line: #d7ccbc;
            --radius: 14px;
            --shadow: 0 12px 36px rgba(31, 29, 27, 0.1);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            color: var(--ink);
            font-family: "Segoe UI", "Trebuchet MS", sans-serif;
            background:
                radial-gradient(circle at 10% -10%, #f7d4b8 0, transparent 40%),
                radial-gradient(circle at 90% -20%, #c7e6de 0, transparent 45%),
                var(--paper);
            min-height: 100vh;
        }}
        .shell {{
            width: min(1200px, 94vw);
            margin: 28px auto;
            background: rgba(255, 255, 255, 0.72);
            backdrop-filter: blur(4px);
            border: 1px solid rgba(215, 204, 188, 0.9);
            border-radius: 24px;
            box-shadow: var(--shadow);
            overflow: hidden;
        }}
        .hero {{
            padding: 24px;
            background: linear-gradient(120deg, #174f4d, #1f6f6d 70%, #b7602a);
            color: #f7f8f4;
        }}
        .hero h1 {{
            margin: 0;
            font-size: clamp(1.4rem, 2vw, 2rem);
            letter-spacing: 0.02em;
        }}
        .hero p {{
            margin: 8px 0 0;
            opacity: 0.92;
        }}
        .filters {{
            padding: 20px 24px;
            background: linear-gradient(180deg, rgba(242, 236, 224, 0.5), transparent);
            border-bottom: 1px solid var(--line);
        }}
        .filters form {{
            display: grid;
            gap: 12px;
            grid-template-columns: 1.8fr 0.8fr 1fr auto;
        }}
        input, select {{
            width: 100%;
            padding: 10px 12px;
            border-radius: var(--radius);
            border: 1px solid var(--line);
            background: #fff;
            color: var(--ink);
            font-size: 0.95rem;
        }}
        button {{
            border: 0;
            border-radius: var(--radius);
            padding: 10px 16px;
            font-weight: 600;
            color: #fff;
            background: var(--accent);
            cursor: pointer;
        }}
        .table-wrap {{ padding: 18px 24px 26px; overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; min-width: 920px; }}
        th, td {{ padding: 10px 8px; text-align: left; border-bottom: 1px solid var(--line); }}
        th {{
            color: #51483f;
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}
        td {{ font-size: 0.93rem; }}
        .mono {{ font-family: Consolas, "Lucida Console", monospace; }}
        .actions {{ white-space: nowrap; }}
        .btn {{
            display: inline-block;
            text-decoration: none;
            padding: 6px 10px;
            border-radius: 999px;
            background: var(--accent);
            color: #fff;
            font-size: 0.85rem;
            margin-right: 6px;
        }}
        .btn.subtle {{ background: var(--warm); }}
        .empty {{
            padding: 20px;
            border: 1px dashed var(--line);
            border-radius: var(--radius);
            background: var(--paper-soft);
            text-align: center;
            color: #6f6154;
        }}
        @media (max-width: 860px) {{
            .filters form {{ grid-template-columns: 1fr; }}
            button {{ width: 100%; }}
        }}
    </style>
</head>
<body>
    <div class="shell">
        <section class="hero">
            <h1>Document Browser</h1>
            <p>Search, filter, and open filed PDFs from your docorganizer index.</p>
        </section>
        <section class="filters">
            <form method="get" action="/">
                <input type="search" name="q" value="{escape(query)}" placeholder="Search terms (FTS)" />
                <select name="status">{''.join(_status_option(s) for s in status_options)}</select>
                <select name="category">{''.join(category_options)}</select>
                <button type="submit">Search</button>
            </form>
        </section>
        <section class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Filename</th>
                        <th>Date</th>
                        <th>Category</th>
                        <th>Source</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(row_html)}
                </tbody>
            </table>
        </section>
    </div>
</body>
</html>
"""


def _render_detail(row, file_exists: bool) -> str:
    view_btn = ""
    if file_exists:
        view_btn = (
            f'<a class="btn" href="/documents/{row["id"]}/content" '
            'target="_blank" rel="noopener noreferrer">Open Document</a>'
        )

    extracted_fields = parse_extracted_fields(row["extracted_fields"])
    ai_sections: list[str] = []
    if row["ai_rationale"]:
        ai_sections.append(
            '<section class="ai-card">'
            "<strong>AI rationale</strong>"
            f"<p>{escape(row['ai_rationale'])}</p>"
            "</section>"
        )
    if row["ai_summary"]:
        ai_sections.append(
            '<section class="ai-card">'
            "<strong>Detailed summary</strong>"
            f"<p>{escape(row['ai_summary'])}</p>"
            "</section>"
        )
    if extracted_fields:
        field_rows = "".join(
            f"<div><dt>{escape(field_name.replace('_', ' ').title())}</dt><dd>{escape(field_value)}</dd></div>"
            for field_name, field_value in extracted_fields.items()
        )
        ai_sections.append(
            '<section class="ai-card">'
            "<strong>Extracted fields</strong>"
            f'<dl class="field-grid">{field_rows}</dl>'
            "</section>"
        )
    ai_block = f'<div class="ai-stack">{"".join(ai_sections)}</div>' if ai_sections else ""

    return f"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>docorg document #{row['id']}</title>
    <style>
        :root {{
            --ink: #241f1b;
            --paper: #fcf9f2;
            --line: #dfd1bf;
            --accent: #2f6f73;
            --warn: #9a3f2e;
            --radius: 14px;
        }}
        body {{
            margin: 0;
            font-family: "Segoe UI", "Trebuchet MS", sans-serif;
            background: linear-gradient(180deg, #f7f0e3, #fcf9f2);
            color: var(--ink);
        }}
        main {{
            width: min(860px, 92vw);
            margin: 28px auto;
            background: #fff;
            border: 1px solid var(--line);
            border-radius: 20px;
            padding: 24px;
        }}
        h1 {{ margin-top: 0; font-size: 1.4rem; }}
        dl {{
            display: grid;
            grid-template-columns: 170px 1fr;
            gap: 10px 14px;
            margin: 18px 0 24px;
        }}
        dt {{ color: #6c5f53; font-weight: 600; }}
        dd {{ margin: 0; overflow-wrap: anywhere; }}
        .ai-stack {{
            display: grid;
            gap: 12px;
            margin-bottom: 20px;
        }}
        .ai-card {{
            padding: 14px 16px;
            border-radius: 12px;
            background: #edf7f2;
            border: 1px solid #b3ddc8;
        }}
        .ai-card strong {{
            display: block;
            color: #2f6f73;
            margin-bottom: 6px;
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}
        .ai-card p {{ margin: 0; line-height: 1.5; }}
        .field-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 10px 14px;
            margin: 0;
        }}
        .field-grid div {{
            padding: 10px 12px;
            background: rgba(255, 255, 255, 0.55);
            border-radius: 10px;
        }}
        .field-grid dt {{
            color: #4f665f;
            margin-bottom: 4px;
        }}
        .field-grid dd {{ margin: 0; }}
        .toolbar {{ display: flex; gap: 10px; flex-wrap: wrap; }}
        .btn {{
            display: inline-block;
            padding: 8px 14px;
            border-radius: 999px;
            text-decoration: none;
            background: var(--accent);
            color: #fff;
            font-weight: 600;
        }}
        .btn.ghost {{ background: #62574c; }}
        .notice {{
            margin-top: 14px;
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid #ebd0cb;
            background: #fff1ef;
            color: var(--warn);
        }}
        @media (max-width: 700px) {{
            dl {{ grid-template-columns: 1fr; gap: 6px; }}
            dt {{ margin-top: 10px; }}
        }}
    </style>
</head>
<body>
    <main>
        <h1>Document #{row['id']} - {escape(_fmt(row['filename']))}</h1>
        {ai_block}
        <dl>
            <dt>Date</dt><dd>{escape(_fmt(row['detected_date']))}</dd>
            <dt>Category</dt><dd>{escape(_fmt(row['category']))}</dd>
            <dt>Source</dt><dd>{escape(_fmt(row['classification_source']))}</dd>
            <dt>Status</dt><dd>{escape(_fmt(row['filing_status']))}</dd>
            <dt>Skipped</dt><dd>{'yes' if row['skipped'] else 'no'}</dd>
            <dt>Created</dt><dd>{escape(_fmt(row['created_at']))}</dd>
            <dt>Reviewed</dt><dd>{escape(_fmt(row['last_reviewed_at']))}</dd>
            <dt>Path</dt><dd>{escape(_fmt(row['filepath']))}</dd>
        </dl>
        <div class="toolbar">
            <a class="btn ghost" href="/">Back</a>
            {view_btn}
        </div>
        {'' if file_exists else '<div class="notice">Document file is missing on disk. Check the stored filepath.</div>'}
    </main>
</body>
</html>
"""


def create_app(cfg: dict) -> FastAPI:
    app = FastAPI(title="docorg web")
    db_path = cfg["paths"]["database"]
    web_cfg = cfg.get("web", {})
    path_rewrites: list[dict] = web_cfg.get("path_rewrite", [])
    path_base_from: str | None = web_cfg.get("path_base_from")
    path_base_to: str | None = web_cfg.get("path_base_to")

    @app.get("/", response_class=HTMLResponse)
    def home(
        q: str = Query(default="", max_length=200),
        status: str = Query(default="all", pattern="^(all|pending|filed)$"),
        category: str | None = Query(default=None),
    ) -> HTMLResponse:
        with get_connection(db_path) as conn:
            if q.strip():
                rows = search_documents(conn, q.strip())
                if status != "all":
                    rows = [row for row in rows if row["filing_status"] == status]
                if category:
                    rows = [row for row in rows if row["category"] == category]
            else:
                rows = list_documents(conn, status=status, category=category)
        return HTMLResponse(_render_home(cfg, q, status, category, rows))

    @app.get("/documents/{doc_id}", response_class=HTMLResponse)
    def document_detail(doc_id: int) -> HTMLResponse:
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        doc_path = _resolve_db_filepath(
            row["filepath"], cfg, path_base_from, path_base_to, path_rewrites
        )
        file_exists = doc_path.exists()
        return HTMLResponse(_render_detail(row, file_exists))

    @app.get("/documents/{doc_id}/content")
    def document_content(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")

        doc_path = _resolve_db_filepath(
            row["filepath"], cfg, path_base_from, path_base_to, path_rewrites
        )
        if not doc_path.exists() or not doc_path.is_file():
            raise HTTPException(status_code=404, detail="Document file is missing")

        media_type = "application/pdf" if doc_path.suffix.lower() == ".pdf" else "application/octet-stream"
        return FileResponse(path=doc_path, filename=doc_path.name, media_type=media_type)

    return app
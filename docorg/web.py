from __future__ import annotations

import json
import tempfile
import urllib.parse
from contextlib import asynccontextmanager
from datetime import date
from html import escape
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .ai import (
    DEFAULT_AI_SETTINGS,
    PROVIDER_DEFAULTS,
    load_ai_settings,
    resolve_ai_config,
    save_ai_settings,
    suggest_date_category,
    test_provider_connection,
)
from .config import add_configured_category, get_configured_categories, remove_configured_category
from .database import (
    get_connection,
    get_document_by_id,
    list_categories,
    list_documents,
    parse_extracted_fields,
    search_documents,
    update_document_fields,
)
from .filer import file_document
from .pathing import resolve_stored_path, to_stored_path
from .processor import process_pdf
from .watcher import start_observer


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


def _pagination_controls(page: int, total: int, page_size: int,
                         q: str, status: str, category: str | None) -> str:
    """Return an HTML pagination bar, or empty string when everything fits on one page."""
    if total <= page_size:
        return ""
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size + 1
    end = min(page * page_size, total)

    def page_url(p: int) -> str:
        params = []
        if q:
            params.append("q=" + urllib.parse.quote_plus(q))
        if status and status != "all":
            params.append("status=" + urllib.parse.quote_plus(status))
        if category:
            params.append("category=" + urllib.parse.quote_plus(category))
        params.append(f"page={p}")
        return "/?" + "&".join(params)

    visible: set[int] = {1, total_pages}
    for p in range(max(1, page - 2), min(total_pages, page + 2) + 1):
        visible.add(p)

    links: list[str] = []
    if page > 1:
        links.append(f'<a class="page-link" href="{page_url(page - 1)}">&#8592; Prev</a>')
    else:
        links.append('<span class="page-link disabled">&#8592; Prev</span>')

    last = 0
    for p in sorted(visible):
        if p - last > 1:
            links.append('<span class="page-ellipsis">&#8230;</span>')
        if p == page:
            links.append(f'<span class="page-link current">{p}</span>')
        else:
            links.append(f'<a class="page-link" href="{page_url(p)}">{p}</a>')
        last = p

    if page < total_pages:
        links.append(f'<a class="page-link" href="{page_url(page + 1)}">Next &#8594;</a>')
    else:
        links.append('<span class="page-link disabled">Next &#8594;</span>')

    count_text = f'<span class="result-count">Showing {start}&#8211;{end} of {total}</span>'
    return f'<div class="pager">{count_text}{" ".join(links)}</div>'


_STYLE = """
:root {
    --ink: #1f1d1b;
    --paper: #f8f4eb;
    --paper-soft: #f2ece0;
    --accent: #1f6f6d;
    --accent-soft: #dcefeb;
    --warm: #b7602a;
    --warn: #9a3f2e;
    --danger: #b23a2e;
    --line: #d7ccbc;
    --radius: 14px;
    --shadow: 0 12px 36px rgba(31, 29, 27, 0.1);
}
* { box-sizing: border-box; }
body {
    margin: 0;
    color: var(--ink);
    font-family: "Segoe UI", "Trebuchet MS", sans-serif;
    background:
        radial-gradient(circle at 10% -10%, #f7d4b8 0, transparent 40%),
        radial-gradient(circle at 90% -20%, #c7e6de 0, transparent 45%),
        var(--paper);
    min-height: 100vh;
}
.shell {
    width: min(1200px, 94vw);
    margin: 28px auto;
    background: rgba(255, 255, 255, 0.72);
    backdrop-filter: blur(4px);
    border: 1px solid rgba(215, 204, 188, 0.9);
    border-radius: 24px;
    box-shadow: var(--shadow);
    overflow: hidden;
}
.hero {
    padding: 24px;
    background: linear-gradient(120deg, #174f4d, #1f6f6d 70%, #b7602a);
    color: #f7f8f4;
}
.hero h1 { margin: 0; font-size: clamp(1.4rem, 2vw, 2rem); letter-spacing: 0.02em; }
.hero p { margin: 8px 0 0; opacity: 0.92; }
.nav { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
.nav-link {
    color: #f7f8f4;
    text-decoration: none;
    padding: 6px 12px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.14);
    font-size: 0.88rem;
    font-weight: 600;
}
.nav-link:hover { background: rgba(255, 255, 255, 0.26); }
.nav-link.current { background: #f7f8f4; color: #174f4d; }
.filters {
    padding: 20px 24px;
    background: linear-gradient(180deg, rgba(242, 236, 224, 0.5), transparent);
    border-bottom: 1px solid var(--line);
}
.filters form { display: grid; gap: 12px; grid-template-columns: 1.8fr 0.8fr 1fr auto; }
input, select, textarea {
    width: 100%;
    padding: 10px 12px;
    border-radius: var(--radius);
    border: 1px solid var(--line);
    background: #fff;
    color: var(--ink);
    font-size: 0.95rem;
    font-family: inherit;
}
button, .btn {
    border: 0;
    border-radius: var(--radius);
    padding: 10px 16px;
    font-weight: 600;
    color: #fff;
    background: var(--accent);
    cursor: pointer;
    font-size: 0.92rem;
}
button[disabled] { opacity: 0.5; cursor: not-allowed; }
.btn {
    display: inline-block;
    text-decoration: none;
    padding: 6px 10px;
    border-radius: 999px;
    font-size: 0.85rem;
    margin-right: 6px;
}
.btn.subtle { background: var(--warm); }
.btn.ghost { background: #62574c; }
.btn.danger, button.danger { background: var(--danger); }
.table-wrap { padding: 18px 24px 26px; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; min-width: 920px; }
th, td { padding: 10px 8px; text-align: left; border-bottom: 1px solid var(--line); }
th { color: #51483f; font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.08em; }
td { font-size: 0.93rem; }
.mono { font-family: Consolas, "Lucida Console", monospace; }
.actions { white-space: nowrap; }
.empty {
    padding: 20px;
    border: 1px dashed var(--line);
    border-radius: var(--radius);
    background: var(--paper-soft);
    text-align: center;
    color: #6f6154;
}
.badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 600;
    letter-spacing: 0.02em;
}
.src-ai { background: #e8e0f5; color: #5933a8; }
.src-rules { background: var(--accent-soft); color: #1a5c5a; }
.src-manual { background: #fdebd0; color: #8a4010; }
.src-other { background: #eee; color: #555; }
.st-filed { background: #d4edda; color: #1a6630; }
.st-pending { background: #fff3cd; color: #7a5200; }
.st-skipped { background: #e9ecef; color: #555; }
.st-other { background: #eee; color: #555; }
thead tr th {
    position: sticky;
    top: 0;
    background: #fff;
    z-index: 2;
    box-shadow: 0 1px 0 var(--line);
}
.pager {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    padding: 16px 24px;
    border-top: 1px solid var(--line);
}
.result-count { margin-right: auto; font-size: 0.88rem; color: #6f6154; }
.page-link {
    display: inline-block;
    padding: 5px 10px;
    border-radius: var(--radius);
    border: 1px solid var(--line);
    font-size: 0.88rem;
    text-decoration: none;
    color: var(--ink);
    background: #fff;
}
.page-link:hover { background: var(--accent-soft); border-color: var(--accent); }
.page-link.current { background: var(--accent); color: #fff; border-color: var(--accent); }
.page-link.disabled { opacity: 0.4; pointer-events: none; }
.page-ellipsis { padding: 5px 4px; color: #999; font-size: 0.88rem; }
.card {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 20px;
    padding: 24px;
    margin: 24px;
}
.card h1 { margin-top: 0; font-size: 1.4rem; }
dl { display: grid; grid-template-columns: 170px 1fr; gap: 10px 14px; margin: 18px 0 24px; }
dt { color: #6c5f53; font-weight: 600; }
dd { margin: 0; overflow-wrap: anywhere; }
.ai-stack { display: grid; gap: 12px; margin-bottom: 20px; }
.ai-card { padding: 14px 16px; border-radius: 12px; background: #edf7f2; border: 1px solid #b3ddc8; }
.ai-card.pending { background: #f5f1e8; border-color: var(--warm); margin-top: 14px; }
.ai-card strong {
    display: block;
    color: #2f6f73;
    margin-bottom: 6px;
    font-size: 0.9rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
.ai-card p { margin: 0 0 8px; line-height: 1.5; }
.field-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px 14px; margin: 0; }
.field-grid div { padding: 10px 12px; background: rgba(255, 255, 255, 0.55); border-radius: 10px; }
.field-grid dt { color: #4f665f; margin-bottom: 4px; }
.field-grid dd { margin: 0; }
.toolbar { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
.notice {
    margin-top: 14px;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid #ebd0cb;
    background: #fff1ef;
    color: var(--warn);
}
.flash {
    margin: 24px 24px 0;
    padding: 12px 16px;
    border-radius: var(--radius);
    font-size: 0.92rem;
    font-weight: 600;
}
.flash.success { background: #d4edda; color: #1a6630; border: 1px solid #b3ddc8; }
.flash.error { background: #fdebe9; color: var(--danger); border: 1px solid #f0c4bb; }
.action-panel {
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 16px 18px;
    margin-bottom: 16px;
    background: rgba(255, 255, 255, 0.6);
}
.action-panel h3 {
    margin: 0 0 10px;
    font-size: 0.92rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #51483f;
}
.inline-form { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; }
.inline-form .field { flex: 1; min-width: 160px; }
.field label { display: block; font-size: 0.82rem; color: #6c5f53; margin-bottom: 4px; font-weight: 600; }
.hint { font-size: 0.82rem; color: #6f6154; margin-top: 6px; }
.checkbox-row { display: flex; align-items: center; gap: 8px; }
.checkbox-row input { width: auto; }
.category-list { list-style: none; padding: 0; margin: 0 0 20px; display: grid; gap: 8px; }
.category-list li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: #fff;
}
@media (max-width: 860px) {
    .filters form { grid-template-columns: 1fr; }
    button { width: 100%; }
    dl { grid-template-columns: 1fr; gap: 6px; }
    dt { margin-top: 10px; }
}
"""


def _nav(active: str) -> str:
    items = [
        ("home", "/", "Browse"),
        ("settings", "/settings", "AI Settings"),
        ("categories", "/categories", "Categories"),
    ]
    links = []
    for key, href, label in items:
        cls = "nav-link current" if key == active else "nav-link"
        links.append(f'<a class="{cls}" href="{href}">{escape(label)}</a>')
    return f'<nav class="nav">{"".join(links)}</nav>'


def _flash_html(msg: str | None, kind: str) -> str:
    if not msg:
        return ""
    return f'<div class="flash {escape(kind)}">{escape(msg)}</div>'


def _page_shell(*, title: str, hero_title: str, hero_sub: str, active: str, body: str) -> str:
    return f"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)}</title>
    <style>{_STYLE}</style>
</head>
<body>
    <div class="shell">
        <section class="hero">
            <h1>{escape(hero_title)}</h1>
            <p>{escape(hero_sub)}</p>
            {_nav(active)}
        </section>
        {body}
    </div>
</body>
</html>
"""


def _render_home(cfg: dict, query: str, status: str, category: str | None, rows: list,
                 db_categories: list[str] | None = None,
                 page: int = 1, page_size: int = 25, total: int = 0,
                 flash: str | None = None, flash_kind: str = "success") -> str:
    status_options = ["all", "pending", "filed"]
    cfg_cats = cfg.get("categories", [])
    # Merge config-defined categories with any category values present in the DB
    categories = sorted(set(cfg_cats) | set(db_categories or []))

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
                    <td><span class="badge src-{source_key}">{source}</span></td>
                    <td><span class="badge st-{status_key}">{status}</span></td>
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
                    source_key=(_fmt(row["classification_source"], "") or "other").lower(),
                    status=escape(_fmt(row["filing_status"])),
                    status_key=(_fmt(row["filing_status"], "") or "other").lower(),
                )
            )

    pagination = _pagination_controls(page, total, page_size, query, status, category)
    body = f"""
    {_flash_html(flash, flash_kind)}
    <section class="filters">
        <form method="post" action="/upload" enctype="multipart/form-data" class="inline-form" style="margin-bottom:14px;">
            <div class="field"><input type="file" name="files" accept="application/pdf" multiple required /></div>
            <button type="submit">Upload PDFs</button>
        </form>
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
        {pagination}
    </section>
    """
    return _page_shell(
        title="docorg browser",
        hero_title="Document Browser",
        hero_sub="Search, filter, and open filed PDFs from your docorganizer index.",
        active="home",
        body=body,
    )


def _render_detail(row, file_exists: bool, *,
                   flash: str | None = None, flash_kind: str = "success",
                   ai_suggestion: dict | None = None, ai_error: str | None = None) -> str:
    doc_id = row["id"]
    view_btn = ""
    if file_exists:
        view_btn = (
            f'<a class="btn" href="/documents/{doc_id}/content" '
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

    date_value = row["detected_date"] or ""
    category_value = row["category"] or ""
    can_refile = bool(row["detected_date"])
    is_skipped = bool(row["skipped"])

    suggestion_html = ""
    if ai_error:
        suggestion_html = f'<div class="notice">{escape(ai_error)}</div>'
    elif ai_suggestion:
        fields_html = ""
        if ai_suggestion.get("fields"):
            field_rows_2 = "".join(
                f"<div><dt>{escape(k)}</dt><dd>{escape(v)}</dd></div>"
                for k, v in ai_suggestion["fields"].items()
            )
            fields_html = f'<dl class="field-grid">{field_rows_2}</dl>'
        rationale_html = f"<p>{escape(ai_suggestion['rationale'])}</p>" if ai_suggestion.get("rationale") else ""
        summary_html = f"<p>{escape(ai_suggestion['summary'])}</p>" if ai_suggestion.get("summary") else ""
        suggestion_html = f"""
        <div class="ai-card pending">
            <strong>AI suggestion (not yet applied)</strong>
            <p><strong>Date:</strong> {escape(ai_suggestion.get('date') or '(none)')}
               &nbsp; <strong>Category:</strong> {escape(ai_suggestion.get('category') or '(none)')}</p>
            {rationale_html}
            {summary_html}
            {fields_html}
            <form method="post" action="/documents/{doc_id}/apply-ai" style="margin-top:12px;">
                <input type="hidden" name="date" value="{escape(ai_suggestion.get('date') or '')}" />
                <input type="hidden" name="category" value="{escape(ai_suggestion.get('category') or '')}" />
                <input type="hidden" name="rationale" value="{escape(ai_suggestion.get('rationale') or '')}" />
                <input type="hidden" name="summary" value="{escape(ai_suggestion.get('summary') or '')}" />
                <input type="hidden" name="fields_json" value='{escape(json.dumps(ai_suggestion.get("fields") or {}))}' />
                <button type="submit">Apply suggestion</button>
            </form>
        </div>
        """

    skip_control = (
        '<span class="badge st-skipped">Skipped</span>'
        if is_skipped
        else f'<form method="post" action="/documents/{doc_id}/skip"><button type="submit">Mark skipped</button></form>'
    )

    body = f"""
    {_flash_html(flash, flash_kind)}
    <div class="card">
        <h1>Document #{doc_id} - {escape(_fmt(row['filename']))}</h1>
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

        <div class="action-panel">
            <h3>Edit date</h3>
            <form method="post" action="/documents/{doc_id}/date" class="inline-form">
                <div class="field"><input type="date" name="date" value="{escape(date_value)}" required /></div>
                <button type="submit">Save date</button>
            </form>
        </div>

        <div class="action-panel">
            <h3>Edit category</h3>
            <form method="post" action="/documents/{doc_id}/category" class="inline-form">
                <div class="field"><input type="text" name="category" value="{escape(category_value)}" placeholder="(blank clears category)" /></div>
                <button type="submit">Save category</button>
            </form>
        </div>

        <div class="action-panel">
            <h3>AI</h3>
            <form method="post" action="/documents/{doc_id}/ask-ai">
                <button type="submit">Ask AI</button>
            </form>
            {suggestion_html}
        </div>

        <div class="action-panel">
            <h3>Filing</h3>
            <div class="inline-form">
                <form method="post" action="/documents/{doc_id}/refile">
                    <button type="submit" {"" if can_refile else "disabled"}>Re-file to date/category folder</button>
                </form>
                {skip_control}
                <a class="btn danger" href="/documents/{doc_id}/delete-confirm">Delete</a>
            </div>
            {'' if can_refile else '<p class="hint">Set a date before re-filing.</p>'}
        </div>
    </div>
    """
    return _page_shell(
        title=f"docorg document #{doc_id}",
        hero_title=f"Document #{doc_id}",
        hero_sub=_fmt(row["filename"]),
        active="home",
        body=body,
    )


def _render_delete_confirm(row) -> str:
    doc_id = row["id"]
    body = f"""
    <div class="card">
        <h1>Delete document #{doc_id}?</h1>
        <p>This removes the database row for <strong>{escape(_fmt(row['filename']))}</strong>.
           The PDF file on disk is <strong>not</strong> deleted.</p>
        <div class="toolbar">
            <form method="post" action="/documents/{doc_id}/delete">
                <button type="submit" class="danger">Yes, delete this row</button>
            </form>
            <a class="btn ghost" href="/documents/{doc_id}">Cancel</a>
        </div>
    </div>
    """
    return _page_shell(
        title=f"Delete document #{doc_id}",
        hero_title="Confirm delete",
        hero_sub=_fmt(row["filename"]),
        active="home",
        body=body,
    )


def _render_settings(settings: dict, *, flash: str | None = None, flash_kind: str = "success",
                     test_result: tuple[bool, str] | None = None) -> str:
    provider = settings.get("provider", "ollama")
    has_key = bool(settings.get("api_key"))

    def _provider_option(value: str, label: str) -> str:
        selected = " selected" if value == provider else ""
        return f'<option value="{value}"{selected}>{label}</option>'

    test_html = ""
    if test_result is not None:
        ok, message = test_result
        kind = "success" if ok else "error"
        test_html = f'<div class="flash {kind}" style="margin: 16px 0 0;">{escape(message)}</div>'

    key_hint = "A key is currently saved. Leave blank to keep it." if has_key else "No key saved yet."
    is_custom = provider == "custom"
    default_base_url = PROVIDER_DEFAULTS.get(provider, {}).get("base_url", "")
    base_url_placeholder = default_base_url or "e.g. https://api.openai.com/v1"
    base_url_label = "Base URL (required for Custom)" if is_custom else "Base URL (optional)"

    body = f"""
    {_flash_html(flash, flash_kind)}
    <div class="card">
        <h1>AI Settings</h1>
        <p class="hint">Configures the AI provider used by "Ask AI" below.
           Seeded from <code>config.yaml</code>'s <code>ai:</code> block on first load; saved settings here take over after that.</p>
        <form method="post" action="/settings">
            <div class="inline-form">
                <div class="field">
                    <label>Provider</label>
                    <select name="provider">
                        {_provider_option("ollama", "Ollama (local)")}
                        {_provider_option("openrouter", "OpenRouter")}
                        {_provider_option("nvidia", "NVIDIA (build.nvidia.com)")}
                        {_provider_option("mistral", "Mistral")}
                        {_provider_option("deepseek", "DeepSeek")}
                        {_provider_option("gemini", "Google Gemini")}
                        {_provider_option("poe", "Poe")}
                        {_provider_option("custom", "Custom (OpenAI-compatible)")}
                    </select>
                </div>
                <div class="field">
                    <label>Model</label>
                    <input type="text" name="model" value="{escape(str(settings.get('model', '')))}" placeholder="e.g. mistral:7b-instruct" required />
                </div>
            </div>
            <div class="inline-form">
                <div class="field">
                    <label>{base_url_label}</label>
                    <input type="text" name="base_url" value="{escape(str(settings.get('base_url', '')))}" placeholder="{escape(base_url_placeholder)}" />
                </div>
                <div class="field">
                    <label>API key</label>
                    <input type="password" name="api_key" placeholder="{'leave blank to keep saved key' if has_key else 'required for most hosted providers'}" autocomplete="off" />
                </div>
            </div>
            <p class="hint">{key_hint} You can enter multiple keys separated by commas — on a 429 (rate limit) response, the next key is tried automatically.</p>
            <div class="inline-form">
                <div class="field">
                    <label>Timeout (seconds)</label>
                    <input type="number" name="timeout" value="{int(settings.get('timeout', 180))}" min="5" />
                </div>
                <div class="field">
                    <label>Max response tokens</label>
                    <input type="number" name="max_tokens" value="{int(settings.get('max_tokens', 1200))}" min="16" />
                </div>
            </div>
            <div class="checkbox-row" style="margin: 14px 0;">
                <input type="checkbox" name="enabled" id="enabled" {"checked" if settings.get("enabled") else ""} />
                <label for="enabled">Enable AI suggestions</label>
            </div>
            <div class="inline-form">
                <button type="submit">Save settings</button>
                <button type="submit" formaction="/settings/test">Test connection</button>
            </div>
        </form>
        {test_html}
        <p class="hint" style="margin-top:20px;">
            API keys: <a href="https://openrouter.ai" target="_blank" rel="noopener noreferrer">OpenRouter</a>
            &middot; <a href="https://build.nvidia.com" target="_blank" rel="noopener noreferrer">NVIDIA</a>
            &middot; <a href="https://console.mistral.ai/api-keys" target="_blank" rel="noopener noreferrer">Mistral</a>
            &middot; <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener noreferrer">DeepSeek</a>
            &middot; <a href="https://makersuite.google.com/app/apikey" target="_blank" rel="noopener noreferrer">Gemini</a>
            &middot; <a href="https://poe.com/api_key" target="_blank" rel="noopener noreferrer">Poe</a>
        </p>
        <p class="hint">"Custom" points at any OpenAI-compatible <code>/chat/completions</code> endpoint — official OpenAI,
           a self-hosted LiteLLM/vLLM/llama.cpp/LM Studio server, or similar. Local servers usually need no API key.</p>
    </div>
    """
    return _page_shell(
        title="docorg settings",
        hero_title="AI Settings",
        hero_sub="Configure the AI provider used for document suggestions.",
        active="settings",
        body=body,
    )


def _render_categories(configured: list[str], db_only: list[str], *,
                       flash: str | None = None, flash_kind: str = "success") -> str:
    rows = "".join(
        f"""<li><span>{escape(cat)}</span>
            <form method="post" action="/categories/remove" style="margin:0;">
                <input type="hidden" name="name" value="{escape(cat)}" />
                <button type="submit" class="btn subtle" style="padding:4px 10px;">Remove</button>
            </form></li>"""
        for cat in configured
    ) or '<li class="hint">No categories configured yet.</li>'

    db_only_html = ""
    if db_only:
        items = "".join(f"<li>{escape(cat)}</li>" for cat in db_only)
        db_only_html = f"""
        <h3 style="margin-top:24px;">Also seen in the database (not in the config list)</h3>
        <ul class="category-list">{items}</ul>
        """

    body = f"""
    {_flash_html(flash, flash_kind)}
    <div class="card">
        <h1>Categories</h1>
        <p class="hint">Categories used by classification rules and filing. Stored in <code>config.yaml</code>,
           alongside the keyword rules that assign them (edit <code>config.yaml</code> directly to change rules).</p>
        <ul class="category-list">{rows}</ul>
        <form method="post" action="/categories/add" class="inline-form">
            <div class="field"><input type="text" name="name" placeholder="New category name" required /></div>
            <button type="submit">Add category</button>
        </form>
        {db_only_html}
    </div>
    """
    return _page_shell(
        title="docorg categories",
        hero_title="Categories",
        hero_sub="Manage the categories used for classification and filing.",
        active="categories",
        body=body,
    )


def create_app(cfg: dict, config_path: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        observer = start_observer(cfg)
        try:
            yield
        finally:
            observer.stop()
            observer.join()

    app = FastAPI(title="docorg web", lifespan=lifespan)
    db_path = cfg["paths"]["database"]
    web_cfg = cfg.get("web", {})
    path_rewrites: list[dict] = web_cfg.get("path_rewrite", [])
    path_base_from: str | None = web_cfg.get("path_base_from")
    path_base_to: str | None = web_cfg.get("path_base_to")
    cfg_path = config_path or Path("config.yaml")

    _PAGE_SIZE = 25

    def _seed_ai_settings(conn) -> dict:
        existing = load_ai_settings(conn)
        if existing is not None:
            return existing
        seed = dict(DEFAULT_AI_SETTINGS)
        legacy = cfg.get("ai", {}) or {}
        seed["enabled"] = bool(legacy.get("enabled", seed["enabled"]))
        seed["model"] = legacy.get("model", seed["model"])
        seed["base_url"] = legacy.get("ollama_url", seed["base_url"])
        seed["timeout"] = legacy.get("timeout", seed["timeout"])
        seed["max_tokens"] = legacy.get("max_tokens", seed["max_tokens"])
        return seed

    def _merge_submitted_ai_settings(existing: dict, *, provider: str, model: str, base_url: str,
                                     api_key: str, timeout: int, max_tokens: int, enabled: bool) -> dict:
        merged = dict(existing)
        merged["provider"] = provider
        merged["model"] = model.strip()

        # The base-URL field has no JS to clear itself when the provider dropdown
        # changes, so a value that just matches the *previous* provider's default
        # is a stale carry-over, not an intentional override — drop it so the new
        # provider's own default applies instead of silently reusing the old host.
        submitted_base_url = base_url.strip()
        previous_default = PROVIDER_DEFAULTS.get(existing.get("provider", ""), {}).get("base_url", "")
        if submitted_base_url and submitted_base_url != previous_default:
            merged["base_url"] = submitted_base_url
        else:
            merged["base_url"] = ""

        merged["timeout"] = timeout
        merged["max_tokens"] = max_tokens
        merged["enabled"] = enabled
        if api_key.strip():
            merged["api_key"] = api_key.strip()
        return merged

    @app.get("/", response_class=HTMLResponse)
    def home(
        q: str = Query(default="", max_length=200),
        status: str = Query(default="all", pattern="^(all|pending|filed)$"),
        category: str | None = Query(default=None),
        page: int = Query(default=1, ge=1),
        msg: str | None = Query(default=None),
        kind: str = Query(default="success"),
    ) -> HTMLResponse:
        with get_connection(db_path) as conn:
            db_categories = list_categories(conn)
            if q.strip():
                rows = search_documents(conn, q.strip())
                if status != "all":
                    rows = [row for row in rows if row["filing_status"] == status]
                if category:
                    rows = [row for row in rows if row["category"] == category]
            else:
                rows = list_documents(conn, status=status, category=category)
        total = len(rows)
        offset = (page - 1) * _PAGE_SIZE
        page_rows = rows[offset: offset + _PAGE_SIZE]
        return HTMLResponse(_render_home(cfg, q, status, category, page_rows,
                                         db_categories=db_categories,
                                         page=page, page_size=_PAGE_SIZE, total=total,
                                         flash=msg, flash_kind=kind))

    @app.post("/upload")
    def upload_documents(files: list[UploadFile] = File(...)):
        # Uploads are staged in a private temp dir, never the watched inbox folder —
        # writing into `inbox` would race the background watcher, which would also
        # pick up the same file and process it concurrently.
        filed = duplicates = not_pdf = failed = 0
        with get_connection(db_path) as conn:
            for upload in files:
                name = Path(upload.filename or "").name
                if not name.lower().endswith(".pdf"):
                    not_pdf += 1
                    continue
                with tempfile.TemporaryDirectory() as tmp_dir:
                    staged = Path(tmp_dir) / name
                    with staged.open("wb") as out:
                        out.write(upload.file.read())
                    try:
                        result = process_pdf(staged, cfg=cfg, conn=conn)
                    except Exception:
                        failed += 1
                        continue
                if result["status"] == "filed":
                    filed += 1
                elif result["status"] == "duplicate":
                    duplicates += 1

        parts = [f"{filed} filed"]
        if duplicates:
            parts.append(f"{duplicates} duplicate")
        if not_pdf:
            parts.append(f"{not_pdf} not a PDF")
        if failed:
            parts.append(f"{failed} failed")
        kind = "success" if not (not_pdf or failed) else "error"
        flash_msg = urllib.parse.quote_plus(", ".join(parts))
        return RedirectResponse(f"/?msg={flash_msg}&kind={kind}", status_code=303)

    @app.get("/documents/{doc_id}", response_class=HTMLResponse)
    def document_detail(doc_id: int, msg: str | None = Query(default=None),
                        kind: str = Query(default="success")) -> HTMLResponse:
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        doc_path = _resolve_db_filepath(
            row["filepath"], cfg, path_base_from, path_base_to, path_rewrites
        )
        file_exists = doc_path.exists()
        return HTMLResponse(_render_detail(row, file_exists, flash=msg, flash_kind=kind))

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

    @app.post("/documents/{doc_id}/date")
    def document_set_date(doc_id: int, date_value: str = Form(..., alias="date")):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            try:
                date.fromisoformat(date_value)
            except ValueError:
                return RedirectResponse(f"/documents/{doc_id}?msg=Invalid+date&kind=error", status_code=303)
            update_document_fields(conn, doc_id, detected_date=date_value,
                                   classification_source="manual", skipped=0)
        return RedirectResponse(f"/documents/{doc_id}?msg=Date+updated", status_code=303)

    @app.post("/documents/{doc_id}/category")
    def document_set_category(doc_id: int, category: str = Form("")):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            value = category.strip() or None
            update_document_fields(conn, doc_id, category=value,
                                   classification_source="manual", skipped=0)
        return RedirectResponse(f"/documents/{doc_id}?msg=Category+updated", status_code=303)

    @app.post("/documents/{doc_id}/ask-ai", response_class=HTMLResponse)
    def document_ask_ai(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            ai_cfg = resolve_ai_config(cfg, conn)
            suggestion = suggest_date_category(
                text=row["extracted_text"] or "",
                filename=row["filename"],
                categories=cfg.get("categories", []),
                ai_cfg=ai_cfg,
            )
            doc_path = _resolve_db_filepath(
                row["filepath"], cfg, path_base_from, path_base_to, path_rewrites
            )
            file_exists = doc_path.exists()
            if not suggestion:
                error = getattr(suggest_date_category, "last_error", "") or "AI suggestion unavailable."
                return HTMLResponse(_render_detail(row, file_exists, ai_error=error))
            return HTMLResponse(_render_detail(row, file_exists, ai_suggestion=suggestion))

    @app.post("/documents/{doc_id}/apply-ai")
    def document_apply_ai(
        doc_id: int,
        date_value: str = Form("", alias="date"),
        category: str = Form(""),
        rationale: str = Form(""),
        summary: str = Form(""),
        fields_json: str = Form("{}"),
    ):
        try:
            fields = json.loads(fields_json) if fields_json else {}
            if not isinstance(fields, dict):
                fields = {}
        except json.JSONDecodeError:
            fields = {}

        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")

            update_document_fields(
                conn,
                doc_id,
                detected_date=date_value or None,
                category=category or None,
                ai_suggested_category=category or None,
                classification_source="ai",
                ai_rationale=rationale or None,
                ai_summary=summary or None,
                extracted_fields=fields or None,
                skipped=0,
            )

            if date_value:
                try:
                    src = resolve_stored_path(row["filepath"], cfg)
                    if src.exists():
                        doc_date = date.fromisoformat(date_value)
                        dest = file_document(
                            src,
                            documents_root=cfg["paths"]["documents"],
                            doc_date=doc_date,
                            category=category or row["category"],
                        )
                        update_document_fields(conn, doc_id, filepath=to_stored_path(dest, cfg))
                except ValueError:
                    pass

        return RedirectResponse(f"/documents/{doc_id}?msg=AI+suggestion+applied", status_code=303)

    @app.post("/documents/{doc_id}/refile")
    def document_refile(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            if not row["detected_date"]:
                return RedirectResponse(
                    f"/documents/{doc_id}?msg=Cannot+refile+without+a+date&kind=error", status_code=303
                )
            src = resolve_stored_path(row["filepath"], cfg)
            if not src.exists():
                return RedirectResponse(
                    f"/documents/{doc_id}?msg=Source+file+missing&kind=error", status_code=303
                )
            doc_date = date.fromisoformat(row["detected_date"])
            dest = file_document(
                src,
                documents_root=cfg["paths"]["documents"],
                doc_date=doc_date,
                category=row["category"],
            )
            update_document_fields(
                conn, doc_id, filepath=to_stored_path(dest, cfg),
                filing_status="filed", skipped=0,
            )
        return RedirectResponse(f"/documents/{doc_id}?msg=Re-filed", status_code=303)

    @app.post("/documents/{doc_id}/skip")
    def document_skip(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            update_document_fields(conn, doc_id, skipped=1)
        return RedirectResponse(f"/documents/{doc_id}?msg=Marked+skipped", status_code=303)

    @app.get("/documents/{doc_id}/delete-confirm", response_class=HTMLResponse)
    def document_delete_confirm(doc_id: int) -> HTMLResponse:
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        return HTMLResponse(_render_delete_confirm(row))

    @app.post("/documents/{doc_id}/delete")
    def document_delete(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.commit()
        return RedirectResponse("/?msg=Document+deleted", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(msg: str | None = Query(default=None), kind: str = Query(default="success")) -> HTMLResponse:
        with get_connection(db_path) as conn:
            settings = _seed_ai_settings(conn)
        return HTMLResponse(_render_settings(settings, flash=msg, flash_kind=kind))

    @app.post("/settings")
    def settings_save(
        provider: str = Form("ollama"),
        model: str = Form(""),
        base_url: str = Form(""),
        api_key: str = Form(""),
        timeout: int = Form(180),
        max_tokens: int = Form(1200),
        enabled: str | None = Form(None),
    ):
        with get_connection(db_path) as conn:
            existing = _seed_ai_settings(conn)
            merged = _merge_submitted_ai_settings(
                existing, provider=provider, model=model, base_url=base_url, api_key=api_key,
                timeout=timeout, max_tokens=max_tokens, enabled=enabled is not None,
            )
            if merged["provider"] == "custom" and not merged["base_url"].strip():
                return RedirectResponse(
                    "/settings?msg=Custom+provider+requires+a+Base+URL&kind=error", status_code=303
                )
            save_ai_settings(conn, merged)
        return RedirectResponse("/settings?msg=Settings+saved", status_code=303)

    @app.post("/settings/test", response_class=HTMLResponse)
    def settings_test(
        provider: str = Form("ollama"),
        model: str = Form(""),
        base_url: str = Form(""),
        api_key: str = Form(""),
        timeout: int = Form(180),
        max_tokens: int = Form(1200),
        enabled: str | None = Form(None),
    ) -> HTMLResponse:
        with get_connection(db_path) as conn:
            existing = _seed_ai_settings(conn)
        merged = _merge_submitted_ai_settings(
            existing, provider=provider, model=model, base_url=base_url, api_key=api_key,
            timeout=timeout, max_tokens=max_tokens, enabled=enabled is not None,
        )
        if merged["provider"] == "custom" and not merged["base_url"].strip():
            return HTMLResponse(_render_settings(
                merged, test_result=(False, "Custom provider requires a Base URL.")
            ))
        ok, message = test_provider_connection(merged)
        return HTMLResponse(_render_settings(merged, test_result=(ok, message)))

    @app.get("/categories", response_class=HTMLResponse)
    def categories_page(msg: str | None = Query(default=None), kind: str = Query(default="success")) -> HTMLResponse:
        configured = get_configured_categories(cfg_path)
        with get_connection(db_path) as conn:
            db_cats = list_categories(conn)
        db_only = sorted(set(db_cats) - set(configured))
        return HTMLResponse(_render_categories(configured, db_only, flash=msg, flash_kind=kind))

    @app.post("/categories/add")
    def categories_add(name: str = Form(...)):
        cleaned = name.strip()
        if not cleaned:
            return RedirectResponse("/categories?msg=Name+required&kind=error", status_code=303)
        added = add_configured_category(cfg_path, cleaned)
        result = "Category+added" if added else "Category+already+exists"
        return RedirectResponse(f"/categories?msg={result}", status_code=303)

    @app.post("/categories/remove")
    def categories_remove(name: str = Form(...)):
        removed = remove_configured_category(cfg_path, name)
        result = "Category+removed" if removed else "Category+not+found"
        return RedirectResponse(f"/categories?msg={result}", status_code=303)

    return app

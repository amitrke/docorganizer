from datetime import date
from pathlib import Path

import click

from .ai import suggest_date_category
from .config import load_config
from .database import (
    get_connection,
    get_document_by_id,
    init_db,
    list_documents,
    search_documents,
    update_document_fields,
)
from .filer import file_document
from .pathing import is_host_neutral_path, resolve_stored_path, to_stored_path
from .processor import analyze_pdf, process_pdf
from .watcher import start_watcher


def _resolve_config(config_path: str) -> dict:
    cfg = load_config(Path(config_path))
    init_db(cfg["paths"]["database"])
    return cfg


@click.group()
def main() -> None:
    """docorg — Document Organizer CLI."""


def _print_filed_result(result: dict) -> None:
    click.echo(
        f"[filed]     {result['filename']}\n"
        f"            -> {result['dest']}\n"
        f"            date={result['detected_date'] or '(fallback)'}  "
        f"category={result['category'] or '(none)'}  "
        f"source={result['classification_source']}"
    )


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _interactive_adjustments(pdf: Path, cfg: dict, conn) -> tuple[
    date | None,
    str | None,
    str | None,
    str | None,
    str | None,
    dict[str, str] | None,
    bool,
]:
    analysis = analyze_pdf(pdf, cfg=cfg, conn=conn)
    if analysis["status"] == "duplicate":
        click.echo(f"[duplicate] {analysis['path']} — skipped.")
        return None, None, None, None, None, None, True

    click.echo(
        f"[analyzed]  {analysis['filename']}\n"
        f"            date={analysis['detected_date'] or '(none)'}  "
        f"category={analysis['category'] or '(none)'}  "
        f"source={analysis['classification_source']}"
    )

    detected_date = analysis["detected_date"]
    category = analysis["category"]
    source = analysis["classification_source"]
    ai_rationale: str | None = None
    ai_summary: str | None = None
    extracted_fields: dict[str, str] | None = None

    while True:
        action = click.prompt(
            "Action",
            type=click.Choice([
                "file",
                "edit-date",
                "edit-category",
                "ask-ai",
                "skip",
            ], case_sensitive=False),
            default="file",
        )

        if action == "file":
            return (
                _parse_iso_date(detected_date),
                category,
                source,
                ai_rationale,
                ai_summary,
                extracted_fields,
                False,
            )
        if action == "skip":
            return (
                _parse_iso_date(detected_date),
                category,
                source,
                ai_rationale,
                ai_summary,
                extracted_fields,
                True,
            )
        if action == "edit-date":
            new_date = click.prompt("Enter date (YYYY-MM-DD)", default=detected_date or "")
            detected_date = new_date or None
            source = "manual"
            click.echo(f"Updated date -> {detected_date or '(none)'}")
            continue
        if action == "edit-category":
            new_cat = click.prompt("Enter category (blank for none)", default=category or "")
            category = new_cat.strip() or None
            source = "manual"
            click.echo(f"Updated category -> {category or '(none)'}")
            continue
        if action == "ask-ai":
            ai_suggestion = suggest_date_category(
                text=analysis.get("text", ""),
                filename=analysis["filename"],
                categories=cfg.get("categories", []),
                ai_cfg=cfg.get("ai", {}),
            )
            if not ai_suggestion:
                click.echo("AI suggestion unavailable (check ai.enabled and Ollama).")
                continue
            click.echo(
                f"AI suggestion: date={ai_suggestion['date'] or '(none)'}  "
                f"category={ai_suggestion['category'] or '(none)'}"
            )
            if ai_suggestion.get("rationale"):
                click.echo(f"Rationale: {ai_suggestion['rationale']}")
            if ai_suggestion.get("summary"):
                click.echo(f"Summary: {ai_suggestion['summary']}")
            if ai_suggestion.get("fields"):
                click.echo("Extracted fields:")
                for field_name, field_value in ai_suggestion["fields"].items():
                    click.echo(f"  {field_name}: {field_value}")
            if click.confirm("Apply AI suggestion?", default=True):
                detected_date = ai_suggestion["date"]
                category = ai_suggestion["category"]
                source = "ai"
                ai_rationale = ai_suggestion.get("rationale") or None
                ai_summary = ai_suggestion.get("summary") or None
                extracted_fields = ai_suggestion.get("fields") or None
            continue


@main.command()
@click.option("--config", default="config.yaml", show_default=True,
              help="Path to config file.")
def watch(config: str) -> None:
    """Start the folder watcher (auto mode)."""
    cfg = _resolve_config(config)
    start_watcher(cfg)


@main.command()
@click.argument("pdf_files", nargs=-1, required=True,
                type=click.Path(exists=True, path_type=Path))
@click.option("--config", default="config.yaml", show_default=True,
              help="Path to config file.")
@click.option("--mode", type=click.Choice(["auto", "interactive"], case_sensitive=False),
              default=None, help="Processing mode override.")
def process(pdf_files: tuple[Path, ...], config: str, mode: str | None) -> None:
    """Process one or more PDF files immediately."""
    cfg = _resolve_config(config)
    selected_mode = (mode or cfg.get("processing", {}).get("mode", "auto")).lower()

    with get_connection(cfg["paths"]["database"]) as conn:
        for pdf in pdf_files:
            if selected_mode == "interactive":
                (
                    override_date,
                    override_category,
                    override_source,
                    override_ai_rationale,
                    override_ai_summary,
                    override_extracted_fields,
                    skip,
                ) = _interactive_adjustments(pdf, cfg, conn)
                if skip and override_date is None and override_category is None and override_source is None:
                    continue
                result = process_pdf(
                    pdf,
                    cfg=cfg,
                    conn=conn,
                    override_date=override_date,
                    override_category=override_category,
                    override_source=override_source,
                    override_ai_rationale=override_ai_rationale,
                    override_ai_summary=override_ai_summary,
                    override_extracted_fields=override_extracted_fields,
                    skip=skip,
                )
            else:
                result = process_pdf(pdf, cfg=cfg, conn=conn)

            if result["status"] == "filed":
                _print_filed_result(result)
            elif result["status"] == "skipped":
                click.echo(f"[skipped]   {result['filename']} — left in inbox.")
            elif result["status"] == "duplicate":
                click.echo(f"[duplicate] {result['path']} — skipped.")


@main.command()
@click.argument("query")
@click.option("--config", default="config.yaml", show_default=True,
              help="Path to config file.")
def search(query: str, config: str) -> None:
    """Full-text search across all indexed documents."""
    cfg = _resolve_config(config)
    with get_connection(cfg["paths"]["database"]) as conn:
        rows = search_documents(conn, query)
    if not rows:
        click.echo("No results.")
        return
    for row in rows:
        click.echo(
            f"{row['filename']:<40}  {row['detected_date'] or '(no date)':>12}"
            f"  {row['category'] or '(no category)':>15}  {row['filepath']}"
        )


@main.command("web")
@click.option("--config", default="config.yaml", show_default=True,
              help="Path to config file.")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Host address to bind.")
@click.option("--port", default=8000, type=int, show_default=True,
              help="Port for the web server.")
def web(config: str, host: str, port: int) -> None:
    """Start a local browser UI for searching and viewing documents."""
    cfg = _resolve_config(config)

    try:
        import uvicorn
    except ImportError as exc:
        raise click.ClickException(
            "Web UI dependencies are missing. Install with: pip install -e \".[web]\""
        ) from exc

    from .web import create_app

    click.echo(f"Starting web UI at http://{host}:{port}")
    uvicorn.run(create_app(cfg), host=host, port=port)


@main.group()
def category() -> None:
    """Manage document categories."""


@category.command("list")
@click.option("--config", default="config.yaml", show_default=True)
def category_list(config: str) -> None:
    """List configured categories."""
    import yaml
    cfg_path = Path(config)
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)
    cats = raw.get("categories", [])
    if not cats:
        click.echo("No categories configured.")
    for cat in cats:
        click.echo(f"  - {cat}")


@category.command("add")
@click.argument("name")
@click.option("--config", default="config.yaml", show_default=True)
def category_add(name: str, config: str) -> None:
    """Add a new category."""
    import yaml
    cfg_path = Path(config)
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)
    cats: list = raw.setdefault("categories", [])
    if name in cats:
        click.echo(f"Category '{name}' already exists.")
        return
    cats.append(name)
    with open(cfg_path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
    click.echo(f"Added category '{name}'.")


@category.command("remove")
@click.argument("name")
@click.option("--config", default="config.yaml", show_default=True)
def category_remove(name: str, config: str) -> None:
    """Remove a category."""
    import yaml
    cfg_path = Path(config)
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)
    cats: list = raw.get("categories", [])
    if name not in cats:
        click.echo(f"Category '{name}' not found.")
        return
    cats.remove(name)
    with open(cfg_path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
    click.echo(f"Removed category '{name}'.")


@main.group()
def review() -> None:
    """Review and correct detected document metadata."""


@review.command("list")
@click.option("--config", default="config.yaml", show_default=True)
@click.option("--status", type=click.Choice(["all", "pending", "filed"], case_sensitive=False), default="all")
@click.option("--category", default=None)
def review_list(config: str, status: str, category: str | None) -> None:
    cfg = _resolve_config(config)
    with get_connection(cfg["paths"]["database"]) as conn:
        rows = list_documents(conn, status=status, category=category)
    if not rows:
        click.echo("No review items.")
        return
    for row in rows:
        click.echo(
            f"#{row['id']:<4} {row['filename']:<35} "
            f"date={row['detected_date'] or '(none)'} "
            f"cat={row['category'] or '(none)'} "
            f"src={row['classification_source']} "
            f"status={row['filing_status']} skipped={row['skipped']}"
        )


@review.command("clear-legacy")
@click.option("--config", default="config.yaml", show_default=True)
@click.option("--apply", is_flag=True,
              help="Delete legacy-path rows from DB. Without this flag, runs as preview.")
@click.option("--limit", default=20, show_default=True, type=int,
              help="How many sample rows to print in preview mode.")
def review_clear_legacy(config: str, apply: bool, limit: int) -> None:
    """Preview or delete rows with legacy (host-specific) filepath values."""
    cfg = _resolve_config(config)
    with get_connection(cfg["paths"]["database"]) as conn:
        rows = conn.execute(
            "SELECT id, filename, filepath FROM documents ORDER BY id"
        ).fetchall()
        legacy_rows = [row for row in rows if not is_host_neutral_path(row["filepath"])]

        if not legacy_rows:
            click.echo("No legacy DB path rows found.")
            return

        click.echo(f"Found {len(legacy_rows)} legacy row(s).")

        if not apply:
            click.echo("Preview (not deleted):")
            for row in legacy_rows[: max(1, limit)]:
                click.echo(f"  #{row['id']:<4} {row['filename']:<35} {row['filepath']}")
            if len(legacy_rows) > limit:
                click.echo(f"  ... and {len(legacy_rows) - limit} more")
            click.echo("Re-run with --apply to delete these legacy rows.")
            return

        conn.executemany(
            "DELETE FROM documents WHERE id = ?",
            [(row["id"],) for row in legacy_rows],
        )
        conn.commit()
        click.echo(f"Deleted {len(legacy_rows)} legacy row(s).")


@review.command("set-date")
@click.option("--config", default="config.yaml", show_default=True)
@click.argument("doc_id", type=int)
@click.argument("new_date")
def review_set_date(config: str, doc_id: int, new_date: str) -> None:
    date.fromisoformat(new_date)
    cfg = _resolve_config(config)
    with get_connection(cfg["paths"]["database"]) as conn:
        update_document_fields(
            conn,
            doc_id,
            detected_date=new_date,
            classification_source="manual",
            clear_ai_metadata=True,
            skipped=0,
        )
    click.echo(f"Updated doc #{doc_id} date -> {new_date}")


@review.command("set-category")
@click.option("--config", default="config.yaml", show_default=True)
@click.argument("doc_id", type=int)
@click.argument("new_category")
def review_set_category(config: str, doc_id: int, new_category: str) -> None:
    cfg = _resolve_config(config)
    category_value = new_category.strip() or None
    with get_connection(cfg["paths"]["database"]) as conn:
        update_document_fields(
            conn,
            doc_id,
            category=category_value,
            classification_source="manual",
            clear_ai_metadata=True,
            skipped=0,
        )
    click.echo(f"Updated doc #{doc_id} category -> {category_value or '(none)'}")


@review.command("skip")
@click.option("--config", default="config.yaml", show_default=True)
@click.argument("doc_id", type=int)
def review_skip(config: str, doc_id: int) -> None:
    cfg = _resolve_config(config)
    with get_connection(cfg["paths"]["database"]) as conn:
        update_document_fields(conn, doc_id, skipped=1)
    click.echo(f"Marked doc #{doc_id} as skipped.")


@review.command("ask-ai")
@click.option("--config", default="config.yaml", show_default=True)
@click.option("--apply", is_flag=True, help="Apply AI suggestion if available.")
@click.argument("doc_id", type=int)
def review_ask_ai(config: str, apply: bool, doc_id: int) -> None:
    cfg = _resolve_config(config)
    with get_connection(cfg["paths"]["database"]) as conn:
        row = get_document_by_id(conn, doc_id)
        if not row:
            click.echo(f"Document #{doc_id} not found.")
            return

        suggestion = suggest_date_category(
            text=row["extracted_text"] or "",
            filename=row["filename"],
            categories=cfg.get("categories", []),
            ai_cfg=cfg.get("ai", {}),
        )

        if not suggestion:
            click.echo("AI suggestion unavailable (check ai.enabled and Ollama).")
            return

        click.echo(
            f"AI suggestion for #{doc_id}: date={suggestion['date'] or '(none)'} "
            f"category={suggestion['category'] or '(none)'}"
        )
        if suggestion.get("rationale"):
            click.echo(f"Rationale: {suggestion['rationale']}")
        if suggestion.get("summary"):
            click.echo(f"Summary: {suggestion['summary']}")
        if suggestion.get("fields"):
            click.echo("Extracted fields:")
            for field_name, field_value in suggestion["fields"].items():
                click.echo(f"  {field_name}: {field_value}")

        if apply:
            update_document_fields(
                conn,
                doc_id,
                detected_date=suggestion["date"],
                category=suggestion["category"],
                classification_source="ai",
                ai_rationale=suggestion.get("rationale") or None,
                ai_summary=suggestion.get("summary") or None,
                extracted_fields=suggestion.get("fields") or None,
                skipped=0,
            )
            click.echo("Applied AI suggestion.")


@review.command("tui")
@click.option("--config", default="config.yaml", show_default=True)
@click.option("--status", type=click.Choice(["all", "pending", "filed"], case_sensitive=False),
              default="all", show_default=True)
@click.option("--category", default=None, help="Filter to a specific category.")
def review_tui(config: str, status: str, category: str | None) -> None:
    """Open the full-screen interactive review TUI."""
    from .tui import launch_review_tui
    cfg = _resolve_config(config)
    launch_review_tui(cfg, status_filter=status, category_filter=category)


@review.command("refile")
@click.option("--config", default="config.yaml", show_default=True)
@click.argument("doc_id", type=int)
def review_refile(config: str, doc_id: int) -> None:
    cfg = _resolve_config(config)
    with get_connection(cfg["paths"]["database"]) as conn:
        row = get_document_by_id(conn, doc_id)
        if not row:
            click.echo(f"Document #{doc_id} not found.")
            return
        if not row["detected_date"]:
            click.echo("Cannot re-file without a detected date. Set date first.")
            return

        src = resolve_stored_path(row["filepath"], cfg)
        if not src.exists():
            click.echo(f"Source file missing: {src}")
            return

        doc_date = date.fromisoformat(row["detected_date"])
        dest = file_document(
            src,
            documents_root=cfg["paths"]["documents"],
            doc_date=doc_date,
            category=row["category"],
        )
        update_document_fields(
            conn,
            doc_id,
            filepath=to_stored_path(dest, cfg),
            filing_status="filed",
            skipped=0,
        )

    click.echo(f"Re-filed #{doc_id} -> {dest}")

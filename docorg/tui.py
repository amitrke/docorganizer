"""Full-screen Textual TUI for the docorg review workflow.

Launch via:  docorg review tui
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from .database import get_connection, list_documents, parse_extracted_fields, update_document_fields
from .filer import file_document
from .pathing import resolve_stored_path, to_stored_path


# ---------------------------------------------------------------------------
# Modal screens
# ---------------------------------------------------------------------------


class EditValueScreen(ModalScreen[str | None]):
    """Generic single-line edit modal.  Dismisses with the new value or None."""

    DEFAULT_CSS = """
    EditValueScreen {
        align: center middle;
    }
    #dialog {
        background: $surface;
        border: round $primary;
        padding: 1 2;
        width: 64;
        height: auto;
    }
    #hint {
        color: $text-muted;
        margin-bottom: 1;
    }
    #buttons {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    Button {
        margin-left: 1;
    }
    """

    def __init__(self, title: str, current_value: str = "", hint: str = "") -> None:
        super().__init__()
        self._title = title
        self._current = current_value
        self._hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title)
            if self._hint:
                yield Label(self._hint, id="hint")
            yield Input(value=self._current, id="value_input")
            with Horizontal(id="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#value_input", Input).focus()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self.query_one("#value_input", Input).value.strip())

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def _submitted(self) -> None:
        self.dismiss(self.query_one("#value_input", Input).value.strip())


class AiSuggestionScreen(ModalScreen[bool]):
    """Show AI suggestion and let user apply or cancel."""

    DEFAULT_CSS = """
    AiSuggestionScreen {
        align: center middle;
    }
    #dialog {
        background: $surface;
        border: round $success;
        padding: 1 2;
        width: 72;
        height: auto;
    }
    #body {
        margin: 1 0;
    }
    #buttons {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    Button {
        margin-left: 1;
    }
    """

    def __init__(self, suggestion: dict) -> None:
        super().__init__()
        self._suggestion = suggestion

    def compose(self) -> ComposeResult:
        s = self._suggestion
        date_str = s.get("date") or "(none)"
        cat_str = s.get("category") or "(none)"
        rationale = s.get("rationale") or ""
        summary = s.get("summary") or ""
        fields = s.get("fields") or {}
        body = f"Date:     {date_str}\nCategory: {cat_str}"
        if rationale:
            body += f"\n\nRationale: {rationale}"
        if summary:
            body += f"\n\nSummary: {summary}"
        if fields:
            rendered_fields = "\n".join(f"{name}: {value}" for name, value in fields.items())
            body += f"\n\nFields:\n{rendered_fields}"
        with Vertical(id="dialog"):
            yield Label("[bold]AI Suggestion[/bold]")
            yield Static(body, id="body")
            with Horizontal(id="buttons"):
                yield Button("Apply", variant="success", id="apply")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#apply")
    def _apply(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(False)


class ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no confirmation modal."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #dialog {
        background: $surface;
        border: round $warning;
        padding: 1 2;
        width: 64;
        height: auto;
    }
    #buttons {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    Button {
        margin-left: 1;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._message)
            with Horizontal(id="buttons"):
                yield Button("Yes", variant="warning", id="yes")
                yield Button("No", id="no")

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# Detail panel
# ---------------------------------------------------------------------------


class DetailPanel(ScrollableContainer):
    """Right-hand panel: shows metadata for the highlighted row."""

    DEFAULT_CSS = """
    DetailPanel {
        border: round $accent;
        padding: 1 2;
        height: 100%;
        overflow-y: auto;
    }
    #detail_static {
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="detail_static", markup=True)

    def show(self, row: sqlite3.Row | None) -> None:
        widget = self.query_one("#detail_static", Static)
        if row is None:
            widget.update("[dim]No document selected.[/dim]")
            return
        extracted_fields = parse_extracted_fields(row["extracted_fields"])
        lines = [
            f"[bold]#{row['id']} — {row['filename']}[/bold]",
            "",
            f"[bold]Date:[/bold]      {row['detected_date'] or '(none)'}",
            f"[bold]Category:[/bold]  {row['category'] or '(none)'}",
            f"[bold]Source:[/bold]    {row['classification_source']}",
            f"[bold]Status:[/bold]    {row['filing_status']}",
            f"[bold]Skipped:[/bold]   {'[yellow]yes[/yellow]' if row['skipped'] else 'no'}",
            "",
            f"[bold]Reviewed:[/bold]  {row['last_reviewed_at'] or '[dim](never)[/dim]'}",
            f"[bold]Created:[/bold]   {row['created_at']}",
        ]
        if row["ai_rationale"]:
            lines.extend([
                "",
                f"[bold]AI Rationale:[/bold] {row['ai_rationale']}",
            ])
        if row["ai_summary"]:
            lines.extend([
                "",
                "[bold]AI Summary:[/bold]",
                row["ai_summary"],
            ])
        if extracted_fields:
            lines.extend([
                "",
                "[bold]Extracted Fields:[/bold]",
            ])
            lines.extend(
                f"  {field_name}: {field_value}"
                for field_name, field_value in extracted_fields.items()
            )
        lines.extend([
            "",
            f"[bold]Path:[/bold]",
            f"  [dim]{row['filepath']}[/dim]",
            "",
            "[dim]d[/dim] edit date  "
            "[dim]c[/dim] edit category  "
            "[dim]a[/dim] ask AI  "
            "[dim]r[/dim] refile  "
            "[dim]s[/dim] skip/unskip",
        ])
        widget.update("\n".join(lines))


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

_TABLE_COLUMNS = [
    # (key,                   label,      width)
    ("id",                    "ID",         5),
    ("filename",              "Filename",  38),
    ("detected_date",         "Date",      12),
    ("category",              "Category",  15),
    ("classification_source", "Source",    10),
    ("filing_status",         "Status",     8),
    ("skipped",               "Skip",       4),
]


class ReviewApp(App):
    """docorg full-screen document review application."""

    TITLE = "docorg — Document Review"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "edit_date", "Edit date"),
        Binding("c", "edit_category", "Edit category"),
        Binding("a", "ask_ai", "Ask AI"),
        Binding("r", "refile", "Refile"),
        Binding("s", "toggle_skip", "Skip/unskip"),
        Binding("ctrl+r", "refresh_list", "Refresh", show=False),
    ]

    DEFAULT_CSS = """
    Screen {
        layout: vertical;
    }
    #main_row {
        layout: horizontal;
        height: 1fr;
    }
    #table_pane {
        width: 2fr;
        height: 100%;
        border: round $primary;
    }
    #detail_pane {
        width: 1fr;
        height: 100%;
    }
    DataTable {
        height: 100%;
    }
    """

    def __init__(
        self,
        cfg: dict,
        status_filter: str = "all",
        category_filter: str | None = None,
    ) -> None:
        super().__init__()
        self._cfg = cfg
        self._status_filter = status_filter
        self._category_filter = category_filter
        self._db_path = cfg["paths"]["database"]
        self._rows: list[sqlite3.Row] = []
        self._selected_id: int | None = None

    # ------------------------------------------------------------------
    # Composition & lifecycle
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main_row"):
            with Container(id="table_pane"):
                yield DataTable(id="doc_table", cursor_type="row", zebra_stripes=True)
            yield DetailPanel(id="detail_pane")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#doc_table", DataTable)
        for col_key, label, width in _TABLE_COLUMNS:
            table.add_column(label, key=col_key, width=width)
        self._load_documents()

    # ------------------------------------------------------------------
    # Document list helpers
    # ------------------------------------------------------------------

    def _load_documents(self) -> None:
        with get_connection(self._db_path) as conn:
            self._rows = list_documents(
                conn,
                status=self._status_filter,
                category=self._category_filter,
            )
        table = self.query_one("#doc_table", DataTable)
        table.clear()
        for row in self._rows:
            table.add_row(
                str(row["id"]),
                row["filename"],
                row["detected_date"] or "",
                row["category"] or "",
                row["classification_source"],
                row["filing_status"],
                "Y" if row["skipped"] else "",
                key=str(row["id"]),
            )
        self._update_detail()

    def _current_doc(self) -> sqlite3.Row | None:
        if self._selected_id is not None:
            for row in self._rows:
                if row["id"] == self._selected_id:
                    return row
        return self._rows[0] if self._rows else None

    def _update_detail(self) -> None:
        self.query_one("#detail_pane", DetailPanel).show(self._current_doc())

    # ------------------------------------------------------------------
    # Table events
    # ------------------------------------------------------------------

    @on(DataTable.RowHighlighted)
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value is not None:
            try:
                self._selected_id = int(event.row_key.value)
            except (ValueError, TypeError):
                pass
        self._update_detail()

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key and event.row_key.value is not None:
            try:
                self._selected_id = int(event.row_key.value)
            except (ValueError, TypeError):
                pass
        self._update_detail()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh_list(self) -> None:
        self._load_documents()
        self.notify("List refreshed.")

    def _require_doc(self) -> sqlite3.Row | None:
        doc = self._current_doc()
        if doc is None:
            self.notify("No document selected.", severity="warning")
        return doc

    # --- edit date ---

    def action_edit_date(self) -> None:
        doc = self._require_doc()
        if doc is None:
            return
        doc_id = doc["id"]
        current = doc["detected_date"] or ""
        cats = self._cfg.get("categories", [])

        def _on_result(value: str | None) -> None:
            if value is None:
                return
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    self.notify(f"Invalid date '{value}' — use YYYY-MM-DD.", severity="error")
                    return
            with get_connection(self._db_path) as conn:
                update_document_fields(
                    conn,
                    doc_id,
                    detected_date=value or None,
                    classification_source="manual",
                    clear_ai_metadata=True,
                    skipped=0,
                )
            self._selected_id = doc_id
            self.notify(f"Date updated → {value or '(none)'}")
            self._load_documents()

        self.push_screen(
            EditValueScreen("Edit Date", current_value=current, hint="Format: YYYY-MM-DD  (blank to clear)"),
            _on_result,
        )

    # --- edit category ---

    def action_edit_category(self) -> None:
        doc = self._require_doc()
        if doc is None:
            return
        doc_id = doc["id"]
        current = doc["category"] or ""
        cats = self._cfg.get("categories", [])
        hint = f"Options: {', '.join(cats)}" if cats else "Enter any category or blank to clear"

        def _on_result(value: str | None) -> None:
            if value is None:
                return
            with get_connection(self._db_path) as conn:
                update_document_fields(
                    conn,
                    doc_id,
                    category=value or None,
                    classification_source="manual",
                    clear_ai_metadata=True,
                    skipped=0,
                )
            self._selected_id = doc_id
            self.notify(f"Category updated → {value or '(none)'}")
            self._load_documents()

        self.push_screen(
            EditValueScreen("Edit Category", current_value=current, hint=hint),
            _on_result,
        )

    # --- ask AI ---

    def action_ask_ai(self) -> None:
        doc = self._require_doc()
        if doc is None:
            return
        ai_cfg = self._cfg.get("ai", {})
        if not ai_cfg.get("enabled"):
            self.notify(
                "AI is disabled. Set ai.enabled: true in config.yaml.",
                severity="warning",
            )
            return

        try:
            from .ai import suggest_date_category  # noqa: PLC0415
        except ImportError:
            self.notify("AI module not available.", severity="error")
            return

        self.notify("Querying AI…")
        suggestion = suggest_date_category(
            text=doc["extracted_text"] or "",
            filename=doc["filename"],
            categories=self._cfg.get("categories", []),
            ai_cfg=ai_cfg,
        )
        if suggestion is None:
            self.notify("AI returned no suggestion. Check Ollama is running.", severity="warning")
            return

        doc_id = doc["id"]

        def _on_apply(apply: bool | None) -> None:
            if not apply:
                return
            with get_connection(self._db_path) as conn:
                update_document_fields(
                    conn,
                    doc_id,
                    detected_date=suggestion.get("date"),
                    category=suggestion.get("category"),
                    classification_source="ai",
                    ai_rationale=suggestion.get("rationale") or None,
                    ai_summary=suggestion.get("summary") or None,
                    extracted_fields=suggestion.get("fields") or None,
                    skipped=0,
                )
            self._selected_id = doc_id
            self.notify("AI suggestion applied.")
            self._load_documents()

        self.push_screen(AiSuggestionScreen(suggestion), _on_apply)

    # --- refile ---

    def action_refile(self) -> None:
        doc = self._require_doc()
        if doc is None:
            return
        if not doc["detected_date"]:
            self.notify(
                "Cannot refile: no date set. Press [d] to set a date first.",
                severity="warning",
            )
            return

        src = resolve_stored_path(doc["filepath"], self._cfg)
        if not src.exists():
            self.notify(f"Source file not found:\n{src}", severity="error")
            return

        doc_id = doc["id"]
        doc_date_str = doc["detected_date"]
        doc_category = doc["category"]
        doc_name = doc["filename"]

        def _on_confirm(yes: bool | None) -> None:
            if not yes:
                return
            try:
                doc_date = date.fromisoformat(doc_date_str)
                dest = file_document(
                    src,
                    documents_root=self._cfg["paths"]["documents"],
                    doc_date=doc_date,
                    category=doc_category,
                )
                with get_connection(self._db_path) as conn:
                    update_document_fields(
                        conn,
                        doc_id,
                        filepath=to_stored_path(dest, self._cfg),
                        filing_status="filed",
                        skipped=0,
                    )
                self._selected_id = doc_id
                self.notify(f"Refiled → {dest}")
                self._load_documents()
            except Exception as exc:
                self.notify(f"Refile failed: {exc}", severity="error")

        self.push_screen(
            ConfirmScreen(
                f"Refile '{doc_name}'?\n"
                f"Date: {doc_date_str}  Category: {doc_category or '(none)'}"
            ),
            _on_confirm,
        )

    # --- toggle skip ---

    def action_toggle_skip(self) -> None:
        doc = self._require_doc()
        if doc is None:
            return
        doc_id = doc["id"]
        new_val = 0 if doc["skipped"] else 1
        with get_connection(self._db_path) as conn:
            update_document_fields(conn, doc_id, skipped=new_val)
        self._selected_id = doc_id
        self.notify(f"Doc #{doc_id} marked as {'skipped' if new_val else 'active'}.")
        self._load_documents()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def launch_review_tui(
    cfg: dict,
    status_filter: str = "all",
    category_filter: str | None = None,
) -> None:
    """Launch the full-screen review TUI.  Called from cli.py."""
    try:
        import textual  # noqa: F401
    except ImportError:
        raise SystemExit(
            "The 'textual' package is required for the TUI.\n"
            "Install it with:  pip install 'docorganizer[tui]'"
        )
    ReviewApp(cfg, status_filter=status_filter, category_filter=category_filter).run()

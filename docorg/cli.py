from pathlib import Path

import click

from .config import load_config
from .database import get_connection, init_db, search_documents
from .processor import process_pdf
from .watcher import start_watcher


def _resolve_config(config_path: str) -> dict:
    cfg = load_config(Path(config_path))
    init_db(cfg["paths"]["database"])
    return cfg


@click.group()
def main() -> None:
    """docorg — Document Organizer CLI."""


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
def process(pdf_files: tuple[Path, ...], config: str) -> None:
    """Process one or more PDF files immediately (auto mode)."""
    cfg = _resolve_config(config)
    with get_connection(cfg["paths"]["database"]) as conn:
        for pdf in pdf_files:
            result = process_pdf(pdf, cfg=cfg, conn=conn)
            if result["status"] == "filed":
                click.echo(
                    f"[filed]     {result['filename']}\n"
                    f"            -> {result['dest']}\n"
                    f"            date={result['detected_date'] or '(fallback)'}  "
                    f"category={result['category'] or '(none)'}  "
                    f"source={result['classification_source']}"
                )
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

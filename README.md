# docorganizer

A Python CLI tool that ingests scanned PDF documents, extracts their text, organises them into a `documents/YYYY/MM/<category>/` folder hierarchy, and stores everything in a searchable local SQLite database.

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) (optional — only needed for AI-assisted classification and field extraction)

---

## Installation

```sh
# Clone and enter the repo
git clone <repo-url>
cd docorganizer

# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# Install
pip install -e .
```

> **OCR support**: `pip install -e ".[ocr]"` — also requires Tesseract installed on the system.
>
> When a PDF page has no selectable text, docorganizer automatically falls back to OCR for that page.

---

## Quick start

1. Drop one or more PDFs into the `scans/` folder.
2. Run `docorg watch` — the tool will detect, categorise, and file them automatically.

---

## Commands

### Watch the inbox (auto mode)

Monitors `scans/` for new PDFs and processes each one as it arrives.

```sh
docorg watch
docorg watch --config /path/to/config.yaml   # custom config location
```

### Process files immediately

Process one or more specific PDFs right now, without the watcher.

```sh
docorg process scans/invoice.pdf
docorg process scans/invoice.pdf scans/statement.pdf
docorg process scans/invoice.pdf --mode interactive
```

Interactive mode lets you file as-is, edit detected date/category, ask AI (on-demand), or skip. When AI is used, docorganizer can also store a rationale, a detailed summary, and extracted key-value facts.

### Search indexed documents

Full-text search across all extracted document content.

```sh
docorg search "hospital bill"
docorg search "income tax 2024"
```

Output columns: `filename`, `detected date`, `category`, `filepath`.

### Browse documents in a web UI

Launch a local browser UI to search, filter, and open indexed documents.

```sh
pip install -e ".[web]"
docorg web
docorg web --host 0.0.0.0 --port 8080
```

Open `http://127.0.0.1:8000` in your browser.

### Manage categories

Categories are stored in `config.yaml`. These commands edit the file directly — no code changes needed.

```sh
docorg category list
docorg category add transport
docorg category remove transport
```

### Review and correction workflow

Review indexed documents, edit metadata, trigger AI suggestions on demand, and re-file corrected documents.

```sh
docorg review list --status all
docorg review set-date 12 2026-05-01
docorg review set-category 12 health
docorg review ask-ai 12
docorg review ask-ai 12 --apply
docorg review ask-ai-bulk --status filed --source-filter not-ai
docorg review ask-ai-bulk --source-filter not-ai --from-date 2025-01-01 --to-date 2025-12-31 --apply
docorg review refile 12
docorg review skip 12
docorg review delete 12
docorg review delete --apply 12
docorg review clear-legacy
docorg review clear-legacy --apply
```

`docorg review ask-ai <id>` previews AI suggestions (date, category, rationale, summary, extracted fields) without saving. Add `--apply` to persist the results to the database.

`docorg review ask-ai-bulk` runs AI suggestions over multiple rows using filters (`--status`, `--source-filter`, `--category`, `--from-date`, `--to-date`, `--limit`). It previews by default; add `--apply` to write changes.

When applied, docorganizer stores both the final `category` and an `ai_suggested_category` value so you can keep AI provenance even after later manual category edits.

### Interactive TUI

A full-screen terminal UI for browsing and editing all indexed documents.

```sh
docorg review tui
docorg review tui --status all    # include already-filed documents
```

Key bindings:

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate document list |
| `a` | Ask AI — shows suggestion dialog; press Apply to save |
| `d` | Edit detected date |
| `c` | Edit category |
| `r` | Re-file document to its current date/category folder |
| `s` | Toggle skip flag |
| `Ctrl+R` | Refresh list |
| `q` | Quit |

The `[a]` AI dialog is the fastest way to enrich existing documents with rationale, a detailed summary, and extracted key-value fields (e.g. `total_amount`, `location`). It calls Ollama on demand and lets you review the suggestion before committing.

`docorg review delete` previews matching rows by ID and only removes them from the database when `--apply` is passed. It does not delete the actual PDF files from disk.

`docorg review clear-legacy` previews rows whose `filepath` still uses host-specific legacy values instead of the host-neutral `documents/...` or `inbox/...` format. Pass `--apply` to remove those legacy rows from the database.

---

## Folder structure after filing

```
documents/
  2024/
    03/
      health/
        dr_sharma_prescription.pdf
      tax/
        form16.pdf
    04/
      invoice_001.pdf        ← no category detected
```

If two files land in the same folder with the same name, a counter is appended automatically (`invoice_001_2.pdf`, etc.).

---

## Configuration (`config.yaml`)

```yaml
paths:
  inbox: scans          # watch folder
  documents: documents  # organised output root
  database: docorganizer.db

processing:
  mode: auto            # auto | interactive (interactive = Phase 5)

date_detection:
  # Keywords used to find label-prefixed dates before generic date parsing.
  # Example matches: "Statement Date: 29/04/2026", "Date of Service 2026-04-29".
  keywords:
    - invoice date
    - statement date
    - date of service
    - service date
    - visit date
    - appointment date
    - issued on
    - date:

categories:
  - health
  - tax
  - education
  - insurance
  - utilities
  - finance

# Mapping rules: keywords matched against filename + extracted text.
# Lower priority number = higher precedence when multiple rules match.
rules:
  - keywords: [doctor, clinic, hospital, prescription]
    category: health
    priority: 10
  - keywords: [income tax, itr, form 16, tds]
    category: tax
    priority: 10

ai:
  enabled: false
  model: mistral:7b-instruct   # default (RTX 3080 / >=6 GB VRAM)
  # lighter alternatives: llama3.2:3b, phi3:mini
  ollama_url: http://localhost:11434
  timeout: 180                 # seconds for Ollama response
  max_tokens: 768              # response budget; increase if JSON gets truncated
```

To use AI-assisted classification, set `ai.enabled: true` and ensure Ollama is running with your chosen model pulled (`ollama pull mistral:7b-instruct`). AI suggestions can also persist a detailed summary plus extracted fields such as `total_amount`, `location`, or other explicit document facts when the model can find them reliably.

Date detection priority is: filename date -> keyword-prefixed text date -> generic text date -> file modified date fallback.

---

## Phased delivery

| Phase | Status | Deliverable |
|-------|--------|-------------|
| 1 | ✅ Done | File watcher → text extraction → SQLite storage → folder move |
| 2 | ✅ Done | OCR fallback for non-searchable pages (Tesseract) |
| 3 | ✅ Done | Date detection via regex + keyword heuristics + filename priority |
| 4 | ✅ Done | Category management + mapping rules + category-aware filing |
| 5 | ✅ Done | Interactive ingestion mode (`docorg process --mode interactive`) |
| 6 | ✅ Done (CLI) | Review workflow (`docorg review ...`) for edit, skip, and re-file |
| 7 | ✅ Done (on-demand) | AI-assisted suggestions via Ollama (`docorg review ask-ai`, interactive ask-ai) |
| 8 | ✅ Done | CLI search via SQLite FTS5 (`docorg search`) |
| 9 | ✅ Done (FastAPI MVP) | Browser UI for search, filters, detail, and document view |

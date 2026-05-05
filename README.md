# docorganizer

A Python CLI tool that ingests scanned PDF documents, extracts their text, organises them into a `documents/YYYY/MM/<category>/` folder hierarchy, and stores everything in a searchable local SQLite database.

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) (optional — only needed for AI-assisted classification)

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

> **OCR support** (Phase 2): `pip install -e ".[ocr]"` — also requires Tesseract installed on the system.

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
```

### Search indexed documents

Full-text search across all extracted document content.

```sh
docorg search "hospital bill"
docorg search "income tax 2024"
```

Output columns: `filename`, `detected date`, `category`, `filepath`.

### Manage categories

Categories are stored in `config.yaml`. These commands edit the file directly — no code changes needed.

```sh
docorg category list
docorg category add transport
docorg category remove transport
```

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
```

To use AI-assisted classification, set `ai.enabled: true` and ensure Ollama is running with your chosen model pulled (`ollama pull mistral:7b-instruct`).

Date detection priority is: filename date -> keyword-prefixed text date -> generic text date -> file modified date fallback.

---

## Phased delivery

| Phase | Status | Deliverable |
|-------|--------|-------------|
| 1 | ✅ Done | File watcher → text extraction → SQLite storage → folder move |
| 2 | Planned | OCR fallback (Tesseract) |
| 3 | Planned | Date detection via regex and keyword heuristics |
| 4 | Planned | Category management + mapping rules + category-aware filing |
| 5 | Planned | Interactive ingestion mode (`--mode interactive`) |
| 6 | Planned | TUI review menu (`docorg review`) |
| 7 | Planned | AI-assisted classification (Ollama, on-demand) |
| 8 | Planned | CLI search (FTS5 already wired in Phase 1) |
| 9 | Future  | FastAPI backend + React UI |

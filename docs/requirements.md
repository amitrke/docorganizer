# Document Organizer — Requirements Document

## 1. Overview

A Python-based desktop/CLI tool that ingests scanned PDF documents, extracts their text content, organizes them into a date- and category-based folder hierarchy, and stores them in a searchable local database.

---

## 2. Goals

- Automatically organize scanned PDFs into `YYYY/MM` folder structures, with optional category subfolders.
- Extract and index text from PDFs for fast keyword search.
- Handle PDFs that are not machine-readable via OCR fallback.
- Provide a foundation for a future web-based UI.
- Support configurable document categories and keyword-based category mapping rules.

---

## 3. Functional Requirements

### 3.1 File Ingestion

| ID | Requirement |
|----|-------------|
| F1 | The system shall monitor a designated input folder (e.g., `scans/`) for new PDF files. |
| F2 | The system shall pick up and process each new PDF automatically. |
| F3 | After processing, the system shall move each PDF to its target folder: `documents/YYYY/MM/` when no category is detected, or `documents/YYYY/MM/<category>/` when a category is detected. |

### 3.2 Text Extraction

| ID | Requirement |
|----|-------------|
| F4 | The system shall extract text from searchable PDFs using `PyMuPDF` or `pdfminer.six`. |
| F5 | If extracted text is empty, the system shall fall back to OCR via `Tesseract` (`pytesseract`). |
| F6 | Extracted text shall be stored in the database alongside document metadata. |

### 3.3 Date Detection

| ID | Requirement |
|----|-------------|
| F7 | The system shall attempt to detect a document date using regex patterns (e.g., `YYYY-MM-DD`, `DD/MM/YYYY`). |
| F8 | The system shall recognize keyword-prefixed dates such as "Invoice Date" and "Statement Date". |
| F9 | The detected date shall determine the target `YYYY/MM` folder. |
| F10 | If no date is detected, the system shall fall back to the file's last **modification** date. |

### 3.4 Folder Organization

| ID | Requirement |
|----|-------------|
| F11 | The system shall organize processed documents under a root `documents/` directory. |
| F12 | Folder structure shall follow the pattern `documents/YYYY/MM/` and support optional category subfolders as `documents/YYYY/MM/<category>/`. |
| F13 | The system shall create the target folder if it does not already exist. |
| F13a | If a file with the same name already exists in the target folder, the system shall append a numeric counter to the filename (e.g., `statement.pdf` → `statement_2.pdf`) rather than overwriting or rejecting it. |

### 3.5 Database / Indexing

| ID | Requirement |
|----|-------------|
| F14 | The system shall use a local SQLite database for storage (no external services required). |
| F15 | The database shall use FTS5 (Full-Text Search) for keyword search capability. |
| F16 | Each document record shall contain: `id`, `filename`, `filepath`, `extracted_text`, `detected_date`, `category`, `classification_source`, `filing_status`, `last_reviewed_at`, `skipped`, `created_at`. |
| F17 | The system shall support basic keyword search via a CLI interface. |

### 3.6 Category Management and Mapping

| ID | Requirement |
|----|-------------|
| F18 | The system shall support a configurable list of categories (e.g., `health`, `tax`, `education`) stored in a local config file. |
| F19 | The system shall provide a CLI command to add, list, and remove categories manually. |
| F20 | The system shall support rule-based category mapping where each rule consists of: one or more keywords/phrases, a target category, and a numeric priority (lower number = higher priority). |
| F21 | During processing, the system shall evaluate both the extracted text and the filename against all category mapping rules to infer a category. |
| F22 | If multiple mapping rules match, the system shall select the rule with the lowest numeric priority value and record the matched rule context. |
| F23 | If no mapping rule matches, the document shall be stored without a category folder (date-only path). |

### 3.7 Interactive Ingestion Mode

| ID | Requirement |
|----|-------------|
| F24 | The system shall support a `--mode` flag with two values: `auto` (default) and `interactive`. |
| F25 | In `auto` mode, the system shall silently process each file using detected values with no user input required. This is the default mode for the folder watcher. |
| F26 | In `interactive` mode, ingestion shall pause after each file is analysed and present the same per-file TUI action screen used by `docorg review` (File as-is / Edit date / Edit category / Ask AI / Skip), giving the user an opportunity to correct values before the file is filed. |
| F27 | If the user chooses **Skip** during interactive ingestion, the file shall remain in the inbox and appear in `docorg review` with a `skipped` flag. |
| F28 | Interactive mode shall be the recommended default when processing files manually or on-demand. |

### 3.8 TUI Review Menu

| ID | Requirement |
|----|-------------|
| F30 | The system shall provide a TUI (terminal menu) invoked via `docorg review` that lists **all** documents known to the system — both pending inbox files and previously filed documents. |
| F31 | The menu shall display each entry as a selectable row showing: filename, current date, current category, classification source (`rules` / `ai` / `manual`), and filing status (`pending` / `filed`). |
| F32 | For each selected file, the menu shall offer the following actions: **File as-is**, **Edit date**, **Edit category**, **Ask AI**, **Re-file**, and **Skip**. |
| F33 | The **Re-file** action shall move a previously filed document to the corrected `YYYY/MM/<category>/` path and update the database record accordingly, including a `last_reviewed_at` timestamp. |
| F34 | The **Ask AI** action shall invoke a local lightweight AI model to suggest date and/or category values; it is available for any document regardless of its current confidence level. |
| F35 | The AI suggestion shall be shown as a proposed value with a brief rationale; the user must explicitly confirm or reject it before it is applied. |
| F36 | The TUI shall support filtering the list by status (`pending`, `filed`, `all`) and by category, to help the user focus the review. |
| F37 | The TUI shall allow bulk-confirm for a filtered subset (e.g., all `filed` documents with `rules` source and high confidence) so the user is not forced to step through every record individually. |
| F38 | Files skipped in the TUI shall retain a `skipped` flag and reappear at the top of the next `docorg review` run. |}

### 3.9 AI-Assisted Classification

| ID | Requirement |
|----|-------------|
| F39 | The system shall support an optional local AI model for category and date suggestion, enabled via config flag; it shall not be required for normal operation. |
| F40 | The AI model shall operate entirely offline via a local Ollama endpoint; no network calls to external services shall be made. |
| F41 | The default AI model shall be `mistral:7b-instruct` (Q4), suitable for systems with a dedicated GPU (e.g., RTX 3080); it shall handle date and category extraction in a single prompt pass. Documents are expected to be in English; the prompt shall be tuned accordingly. |
| F42 | The config file shall expose an `ai_model` setting so the user can switch to a lighter alternative (e.g., `llama3.2:3b`, `phi3:mini`) without code changes, for systems with less VRAM or CPU-only inference. |
| F43 | AI classification shall only be invoked on demand (via the **Ask AI** TUI action or `--ai` CLI flag); it shall never run automatically without user intent. |
| F44 | The system shall log whether a filed document's category/date was set by rules, by AI suggestion, or manually, and store this as `classification_source` in the database. |

---

## 4. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NF1 | The system shall run on Python 3.x with no mandatory cloud or external service dependencies. |
| NF2 | Text extraction and OCR shall complete within a reasonable time for typical single-page to 20-page documents. |
| NF3 | The system shall not duplicate records if a file has already been processed. |
| NF4 | Date detection accuracy of 60–70% on real-world documents is acceptable for v1. |
| NF5 | Category mapping shall be configurable without code changes, using local config updates only. |
| NF6 | The TUI shall be keyboard-navigable and must not require a mouse or graphical display. |
| NF7 | The default AI model (`mistral:7b-instruct` Q4 via Ollama) shall respond within a few seconds on a GPU with ≥6GB VRAM; lighter models shall be selectable via config for CPU-only or lower-VRAM machines. |

---

## 5. Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.x |
| PDF text extraction | PyMuPDF or pdfminer.six |
| OCR fallback | Tesseract via pytesseract |
| Database | SQLite with FTS5 |
| File operations | `shutil`, `pathlib` |
| TUI menu | `questionary` or `textual` |
| AI classification (optional) | Ollama (`mistral:7b-instruct` Q4 default; configurable for lighter models e.g. `llama3.2:3b`, `phi3:mini`) |
| Future backend | FastAPI |
| Future frontend | React |

---

## 6. Out of Scope (v1)

- Web UI or API layer.
- Elasticsearch or MongoDB integration.
- Advanced NLP-based date extraction.
- Cloud storage or sync.
- Multi-user support.

---

## 7. Phased Delivery Plan

| Phase | Deliverable |
|-------|-------------|
| Phase 1 | File watcher → text extraction → SQLite storage → folder move |
| Phase 2 | OCR fallback for non-searchable PDFs |
| Phase 3 | Date detection via regex and keyword heuristics |
| Phase 4 | Category management + mapping rules + category-aware filing |
| Phase 5 | Interactive ingestion mode (`--mode interactive`) reusing TUI per-file action screen |
| Phase 6 | TUI review menu (`docorg review`) with per-file actions |
| Phase 7 | AI-assisted classification (on-demand, local model, opt-in) |
| Phase 8 | CLI search tool using SQLite FTS5 |
| Phase 9 | FastAPI backend + React UI (future) |

---

## 8. Success Criteria (v1)

> Drop a PDF into the `scans/` folder -> it is moved to the correct `documents/YYYY/MM/` or `documents/YYYY/MM/<category>/` folder and its text becomes searchable via CLI. For documents where detection is uncertain, `docorg review` presents a menu to resolve them manually or with an AI suggestion.

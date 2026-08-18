# Document Organizer — Requirements Document

## 1. Overview

A self-hosted Python web app (FastAPI, server-rendered — no separate frontend build) that ingests scanned PDF documents, extracts their text content, organizes them into a date- and category-based folder hierarchy, and stores them in a searchable local database. Runs as a single Docker container: it serves the browser UI, watches a mounted inbox folder for new scans in the background, and accepts direct uploads. There is no CLI.

---

## 2. Goals

- Automatically organize scanned PDFs into `YYYY/MM` folder structures, with optional category subfolders.
- Extract and index text from PDFs for fast keyword search.
- Handle PDFs that are not machine-readable via OCR fallback.
- Provide a browser-based UI for browsing, uploading, correcting, and re-filing documents — no CLI required.
- Support configurable document categories and keyword-based category mapping rules.

---

## 3. Functional Requirements

### 3.1 File Ingestion

| ID | Requirement |
|----|-------------|
| F1 | The system shall monitor a designated input folder (e.g., `scans/`) for new PDF files, running as a background task inside the web server process (no separate watcher process). |
| F1a | The system shall also accept PDF uploads directly from the browser UI, processed through the same pipeline as watched-folder files. |
| F2 | The system shall pick up and process each new PDF automatically. |
| F3 | After processing, the system shall move each PDF to its target folder: `documents/YYYY/MM/` when no category is detected, or `documents/YYYY/MM/<category>/` when a category is detected. |

### 3.2 Text Extraction

| ID | Requirement |
|----|-------------|
| F4 | The system shall extract text from searchable PDFs using `PyMuPDF` or `pdfminer.six`. |
| F5 | If extracted text is empty for a page (or entire PDF), the system shall fall back to OCR via `Tesseract` (`pytesseract`). |
| F6 | Extracted text shall be stored in the database alongside document metadata. |

### 3.3 Date Detection

| ID | Requirement |
|----|-------------|
| F7 | The system shall attempt to detect a document date using regex patterns (e.g., `YYYY-MM-DD`, `DD/MM/YYYY`). |
| F8 | The system shall recognize keyword-prefixed dates such as "Invoice Date" and "Statement Date". |
| F8a | Date-detection keywords shall be configurable via local config (no code changes required). |
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
| F17 | The system shall support keyword search via the browser UI's search box. |

### 3.6 Category Management and Mapping

| ID | Requirement |
|----|-------------|
| F18 | The system shall support a configurable list of categories (e.g., `health`, `tax`, `education`) stored in a local config file. |
| F19 | The system shall provide a browser page to add, list, and remove categories manually (mapping rules themselves remain config-file-edited). |
| F20 | The system shall support rule-based category mapping where each rule consists of: one or more keywords/phrases, a target category, and a numeric priority (lower number = higher priority). |
| F21 | During processing, the system shall evaluate both the extracted text and the filename against all category mapping rules to infer a category. |
| F22 | If multiple mapping rules match, the system shall select the rule with the lowest numeric priority value and record the matched rule context. |
| F23 | If no mapping rule matches, the document shall be stored without a category folder (date-only path). |

### 3.7 Ingestion Mode

| ID | Requirement |
|----|-------------|
| F24 | The system shall process every incoming PDF (watched-folder or uploaded) automatically and immediately, using detected values with no ingestion-time gate. There is no separate "interactive ingestion" mode — correction happens afterward on the document's detail page (see 3.8). |
| F27 | A document can be marked skipped from its detail page after filing; skipped documents retain a `skipped` flag and are surfaced first in the browse list. |

### 3.8 Review & Correction (Browser)

| ID | Requirement |
|----|-------------|
| F30 | The system shall provide a browse page listing **all** documents known to the system — both pending inbox files and previously filed documents — with search and status/category filters. |
| F31 | The list and document detail page shall display: filename, current date, current category, classification source (`rules` / `ai` / `manual`), and filing status (`pending` / `filed`). |
| F32 | From a document's detail page, the system shall offer: **edit date**, **edit category**, **Ask AI**, **Re-file**, **mark skipped**, and **delete** (database row only, not the PDF on disk). |
| F33 | The **Re-file** action shall move a previously filed document to the corrected `YYYY/MM/<category>/` path and update the database record accordingly, including a `last_reviewed_at` timestamp. |
| F34 | The **Ask AI** action shall invoke the configured AI provider (see 3.9) to suggest date and/or category values; it is available for any document regardless of its current confidence level. |
| F35 | The AI suggestion shall be shown as a proposed value with a brief rationale; the user must explicitly click Apply before it is persisted. |
| F36 | The browse page shall support filtering the list by status (`pending`, `filed`, `all`) and by category. |
| F38 | Documents marked skipped shall retain a `skipped` flag and be surfaced first in the browse list until resolved. |

### 3.9 AI-Assisted Classification

| ID | Requirement |
|----|-------------|
| F39 | The system shall support an optional AI provider for category and date suggestion, configured from the browser's Settings page; it shall not be required for normal operation. |
| F40 | The system shall support a local Ollama endpoint (fully offline, no external network calls) as well as hosted providers — OpenRouter and NVIDIA (build.nvidia.com) — selected per-deployment via the Settings page. |
| F41 | The default local model is `mistral:7b-instruct` (Q4), suitable for systems with a dedicated GPU (e.g., RTX 3080); it shall handle date and category extraction in a single prompt pass. Documents are expected to be in English; the prompt shall be tuned accordingly. |
| F42 | The Settings page shall expose a `model` field so the user can switch models (local or hosted) without code changes. |
| F43 | AI classification shall only be invoked on demand (via the detail page's **Ask AI** action); it shall never run automatically without user intent. |
| F44 | The system shall log whether a filed document's category/date was set by rules, by AI suggestion, or manually, and store this as `classification_source` in the database. |

---

## 4. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NF1 | The system shall run on Python 3.x; cloud dependencies are opt-in only (hosted AI providers), never required for core ingestion/browse/search. |
| NF2 | Text extraction and OCR shall complete within a reasonable time for typical single-page to 20-page documents. |
| NF3 | The system shall not duplicate records if a file has already been processed. |
| NF4 | Date detection accuracy of 60–70% on real-world documents is acceptable for v1. |
| NF5 | Category mapping shall be configurable without code changes, using local config updates only. |
| NF6 | The browser UI shall be usable with only a keyboard where practical, and must not require a native desktop client. |
| NF7 | The default local AI model (`mistral:7b-instruct` Q4 via Ollama) shall respond within a few seconds on a GPU with ≥6GB VRAM; hosted providers (OpenRouter, NVIDIA) are selectable via the Settings page for systems without a local GPU. |

---

## 5. Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.x |
| PDF text extraction | PyMuPDF or pdfminer.six |
| OCR fallback | Tesseract via pytesseract |
| Database | SQLite with FTS5 |
| File operations | `shutil`, `pathlib` |
| Web backend + UI | FastAPI, server-rendered HTML (no separate frontend build) |
| Inbox watching | `watchdog`, run as a background task inside the web server process |
| AI classification (optional) | Ollama (local, default `mistral:7b-instruct`), or OpenRouter / NVIDIA (hosted, OpenAI-compatible) — selected from the Settings page |

---

## 6. Out of Scope (v1)

- Elasticsearch or MongoDB integration.
- Advanced NLP-based date extraction.
- Cloud storage or sync of the document archive itself.
- Multi-user support / authentication.

---

## 7. Phased Delivery Plan

| Phase | Deliverable |
|-------|-------------|
| Phase 1 | Background inbox watcher → text extraction → SQLite storage → folder move |
| Phase 2 | OCR fallback for non-searchable PDFs |
| Phase 3 | Date detection via regex and keyword heuristics |
| Phase 4 | Category management + mapping rules + category-aware filing |
| Phase 5 | Browser upload as a second ingestion path alongside the watched folder |
| Phase 6 | Browser review workflow (edit date/category, re-file, skip, delete) on the document detail page |
| Phase 7 | AI-assisted classification (on-demand, Settings-page-configured provider, opt-in) |
| Phase 8 | Full-text search via SQLite FTS5, exposed in the browse page |
| Phase 9 | FastAPI browser UI as the sole interface (CLI removed) |

---

## 8. Success Criteria (v1)

> Drop a PDF into the `scans/` folder, or upload it from the browser -> it is moved to the correct `documents/YYYY/MM/` or `documents/YYYY/MM/<category>/` folder and its text becomes searchable from the browse page. For documents where detection is uncertain, the document's detail page lets the user correct the date/category manually or with an AI suggestion.

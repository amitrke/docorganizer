# Document Organizer — Requirements Document

## 1. Overview

A Python-based desktop/CLI tool that ingests scanned PDF documents, extracts their text content, organizes them into a date-based folder hierarchy, and stores them in a searchable local database.

---

## 2. Goals

- Automatically organize scanned PDFs into `YYYY/MM` folder structures.
- Extract and index text from PDFs for fast keyword search.
- Handle PDFs that are not machine-readable via OCR fallback.
- Provide a foundation for a future web-based UI.

---

## 3. Functional Requirements

### 3.1 File Ingestion

| ID | Requirement |
|----|-------------|
| F1 | The system shall monitor a designated input folder (e.g., `scans/`) for new PDF files. |
| F2 | The system shall pick up and process each new PDF automatically. |
| F3 | After processing, the system shall move each PDF to its target `documents/YYYY/MM/` folder. |

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
| F10 | If no date is detected, the system shall fall back to the file's creation/modification date. |

### 3.4 Folder Organization

| ID | Requirement |
|----|-------------|
| F11 | The system shall organize processed documents under a root `documents/` directory. |
| F12 | Folder structure shall follow the pattern `documents/YYYY/MM/`. |
| F13 | The system shall create the target folder if it does not already exist. |

### 3.5 Database / Indexing

| ID | Requirement |
|----|-------------|
| F14 | The system shall use a local SQLite database for storage (no external services required). |
| F15 | The database shall use FTS5 (Full-Text Search) for keyword search capability. |
| F16 | Each document record shall contain: `id`, `filename`, `filepath`, `extracted_text`, `detected_date`, `created_at`. |
| F17 | The system shall support basic keyword search via a CLI interface. |

### 3.6 Interactive Mode

| ID | Requirement |
|----|-------------|
| F18 | The system shall support a `--mode` flag with two values: `auto` (default) and `interactive`. |
| F19 | In `auto` mode, the system shall silently use the detected date (or fall back to file date) with no user input required. This is the default mode for the folder watcher. |
| F20 | In `interactive` mode, the system shall prompt the user to confirm or override the detected date before filing the document. |
| F21 | The interactive prompt shall display: the filename, the detected date (or indicate none found), the confidence context (e.g., number of candidate dates found), and a text field to accept or enter a corrected `YYYY-MM-DD` date. |
| F22 | If the user provides no input at the prompt, the system shall use the detected date as-is. |
| F23 | Interactive mode shall be the recommended default when processing files manually or on-demand. |

---

## 4. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NF1 | The system shall run on Python 3.x with no mandatory cloud or external service dependencies. |
| NF2 | Text extraction and OCR shall complete within a reasonable time for typical single-page to 20-page documents. |
| NF3 | The system shall not duplicate records if a file has already been processed. |
| NF4 | Date detection accuracy of 60–70% on real-world documents is acceptable for v1. |

---

## 5. Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.x |
| PDF text extraction | PyMuPDF or pdfminer.six |
| OCR fallback | Tesseract via pytesseract |
| Database | SQLite with FTS5 |
| File operations | `shutil`, `pathlib` |
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
| Phase 4 | Interactive mode (`--mode interactive`) for date confirmation |
| Phase 5 | CLI search tool using SQLite FTS5 |
| Phase 6 | FastAPI backend + React UI (future) |

---

## 8. Success Criteria (v1)

> Drop a PDF into the `scans/` folder → it is moved to the correct `documents/YYYY/MM/` folder and its text becomes searchable via CLI.

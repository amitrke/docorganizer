# docorganizer

A self-hosted web app that ingests scanned PDF documents, extracts their text, organises them into a `documents/YYYY/MM/<category>/` folder hierarchy, and stores everything in a searchable local SQLite database. Everything happens in the browser — there is no CLI. A single Docker container serves the UI, watches a mounted inbox folder for new scans, and accepts direct uploads.

The backend (`docorg/`, FastAPI) is a JSON API; the UI (`frontend/`) is a React + TypeScript single-page app built with Vite and served by the same container as static assets.

---

## Quick start (Docker)

```sh
docker compose up -d
```

Open `http://localhost:8000`. Then either:

- Drag PDFs into the mounted `scans/` folder (see `docker-compose.yml` for the host path), or
- Use the **Upload PDFs** button on the home page.

Either way, new documents are deduplicated (SHA-256), dated, categorised via the rules in `config.yaml`, and filed under `documents/YYYY/MM/<category>/` automatically. Anything that came out wrong is fixed afterwards from the browser (see below) — nothing needs to be re-run.

---

## What the web UI does

- **Browse & search** — full-text search (SQLite FTS5) across all indexed documents, with status/category filters and date-range presets (last 7/30/90 days, this year, last year, or a custom range).
- **Upload** — drag-and-drop or pick multiple PDFs at once, each with its own live progress bar; they're processed immediately, the same way a dropped file in `scans/` is.
- **Multi-select + bulk Ask AI** — select several documents from the browse table and run them through AI together; suggestions stream in per-document as they're ready, with per-item or "apply all ready" actions.
- **Document detail page** — edit the detected date/category, **Ask AI** for a suggestion (preview it, then Apply), re-file to the correct folder, mark as skipped, or delete the database row (this does not delete the PDF from disk).
- **AI Settings page** — save multiple named AI provider configs (e.g. "Local Ollama", "NVIDIA free") — local **Ollama**, **OpenRouter**, **NVIDIA**, **Mistral**, **DeepSeek**, **Google Gemini**, **Poe**, or any other **custom OpenAI-compatible endpoint** (self-hosted LiteLLM/vLLM/llama.cpp/LM Studio, etc.) — each remembering its own model and API key, and switch which one is active with one click via "Activate". "Test connection" checks a config before or after saving it. Multiple comma-separated API keys within a config rotate automatically on rate limits (HTTP 429).
- **Categories page** — add/remove the categories used by classification rules.

---

## Local development (without Docker)

The backend (JSON API) and frontend (React SPA) run as two separate processes in development — the frontend's dev server proxies `/api` requests to the backend, so there's no CORS setup needed.

**Backend:**

```sh
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -e .
docorg                        # DOCORG_CONFIG=config.yaml, DOCORG_HOST=0.0.0.0, DOCORG_PORT=8000
```

Override with env vars as needed, e.g. `DOCORG_CONFIG=config.yaml DOCORG_HOST=127.0.0.1 DOCORG_PORT=8000 docorg` (`docorg` is a thin wrapper around `python -m docorg`).

> **OCR support**: `pip install -e ".[ocr]"` — also requires Tesseract installed on the system. When a PDF page has no selectable text, docorganizer automatically falls back to OCR for that page.

**Frontend** (in a second terminal):

```sh
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` — this is the dev UI, hot-reloading, proxying API calls to the backend on port 8000 (see `frontend/vite.config.ts`).

**Building for production** (what the Docker image does): `cd frontend && npm run build` produces `frontend/dist/`, which gets copied to `docorg/static/` and served by the backend directly — at that point there's just one process/port again, same as the Docker deployment.

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

If two files land in the same folder with the same name, a counter is appended automatically (`invoice_001_2.pdf`, etc.). Duplicate detection uses SHA-256 content hashing plus existing path checks, so the same PDF content is skipped even when uploaded or dropped with a different filename.

---

## Configuration (`config.yaml`)

```yaml
paths:
  inbox: scans          # watched folder + upload landing zone
  documents: documents  # organised output root
  database: docorganizer.db

classification:
  # Category assignment uses weighted scoring instead of first keyword hit.
  min_score: 1.0        # minimum winning score required
  min_score_gap: 0.75   # winner must beat runner-up by this margin
  negative_weight: 1.25 # penalty multiplier for negative keyword hits

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
# Lower priority number provides a small tie-break bonus.
rules:
  - keywords: [doctor, clinic, hospital, prescription]
    category: health
    priority: 10
  - keywords: [income tax, itr, form 16, tds]
    category: tax
    priority: 10
  - category: education
    any_keywords: [public schools, school district]
    exclude_keywords: [tax return]
    negative_keywords: [form 1040]
    filename_keywords: [school]
    priority: 10

# Optional first-run seed: migrated into a "Default" AI config the first time
# the AI Settings page is opened on a database with none saved yet. After
# that, configs live in the database (see AI Settings in the browser) and
# this block is no longer read.
ai:
  enabled: false
  model: mistral:7b-instruct
  ollama_url: http://localhost:11434
  timeout: 180
  max_tokens: 768
```

Supported rule fields (all optional except `category`):

- `keywords`: positive terms (legacy behavior, still supported)
- `any_keywords`: at least one must match
- `all_keywords`: every term must match
- `filename_keywords`: positive terms checked against filename only (higher weight)
- `exclude_keywords`: hard block list; rule is skipped if any match
- `negative_keywords`: soft penalties to reduce false positives
- `priority`: lower numbers provide a mild bonus when scores are close

Categories and their `rules` are managed by editing `config.yaml` directly (the Categories page in the browser only adds/removes the category list, not the rules); AI provider settings are managed from the browser's Settings page instead.

Date detection priority is: filename date -> keyword-prefixed text date -> generic text date -> file modified date fallback.

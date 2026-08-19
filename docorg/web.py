from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ai import (
    DEFAULT_AI_SETTINGS,
    PROVIDER_DEFAULTS,
    ensure_ai_profiles_seeded,
    resolve_ai_config,
    suggest_date_category,
    test_provider_connection,
)
from .config import add_configured_category, get_configured_categories, remove_configured_category
from .database import (
    SORT_COLUMNS,
    delete_ai_profile,
    get_ai_profile,
    get_connection,
    get_document_by_id,
    insert_ai_profile,
    list_ai_profiles,
    list_categories,
    list_documents,
    parse_extracted_fields,
    search_documents,
    set_active_ai_profile,
    update_ai_profile,
    update_document_fields,
)
from .filer import file_document
from .pathing import resolve_stored_path, to_stored_path
from .processor import process_pdf
from .thumbnail import ensure_thumbnail, thumbnail_path
from .watcher import start_observer


def _valid_iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _sort_rows(rows: list, sort_by: str, sort_dir: str) -> list:
    """Sort FTS results the same way list_documents sorts via SQL: nulls always last."""
    column = sort_by if sort_by in SORT_COLUMNS else "detected_date"
    reverse = sort_dir != "asc"
    with_value = [r for r in rows if r[column] is not None]
    without_value = [r for r in rows if r[column] is None]
    with_value.sort(key=lambda r: r[column], reverse=reverse)
    return with_value + without_value


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


def _doc_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "filepath": row["filepath"],
        "content_hash": row["content_hash"],
        "file_size": row["file_size"],
        "detected_date": row["detected_date"],
        "category": row["category"],
        "ai_suggested_category": row["ai_suggested_category"],
        "classification_source": row["classification_source"],
        "ai_rationale": row["ai_rationale"],
        "ai_summary": row["ai_summary"],
        "extracted_fields": parse_extracted_fields(row["extracted_fields"]),
        "filing_status": row["filing_status"],
        "last_reviewed_at": row["last_reviewed_at"],
        "skipped": bool(row["skipped"]),
        "created_at": row["created_at"],
    }


class DocumentPatch(BaseModel):
    detected_date: str | None = None
    category: str | None = None


class ApplyAiRequest(BaseModel):
    detected_date: str | None = None
    category: str | None = None
    rationale: str | None = None
    summary: str | None = None
    fields: dict[str, str] = {}


class BulkAskAiRequest(BaseModel):
    doc_ids: list[int]


class CategoryCreate(BaseModel):
    name: str


class AiProfileCreate(BaseModel):
    name: str
    provider: str = "ollama"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    timeout: int = 180
    max_tokens: int = 1200


class AiProfileUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout: int | None = None
    max_tokens: int | None = None


class AiProfileTestPayload(BaseModel):
    provider: str = "ollama"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    timeout: int = 180
    max_tokens: int = 1200


def create_app(cfg: dict, config_path: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        observer = start_observer(cfg)
        try:
            yield
        finally:
            observer.stop()
            observer.join()

    app = FastAPI(title="docorg api", lifespan=lifespan)
    db_path = cfg["paths"]["database"]
    web_cfg = cfg.get("web", {})
    path_rewrites: list[dict] = web_cfg.get("path_rewrite", [])
    path_base_from: str | None = web_cfg.get("path_base_from")
    path_base_to: str | None = web_cfg.get("path_base_to")
    cfg_path = config_path or Path("config.yaml")

    _PAGE_SIZE = 25

    def _profile_to_dict(row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "provider": row["provider"],
            "model": row["model"],
            "base_url": row["base_url"],
            "timeout": row["timeout"],
            "max_tokens": row["max_tokens"],
            "is_active": bool(row["is_active"]),
            "has_key": bool(row["api_key"]),
        }

    api = APIRouter(prefix="/api")

    @api.get("/documents")
    def api_list_documents(
        q: str = Query(default="", max_length=200),
        status: str = Query(default="all", pattern="^(all|pending|filed)$"),
        category: str | None = Query(default=None),
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
        sort_by: str = Query(default="detected_date"),
        sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
        page: int = Query(default=1, ge=1),
    ):
        resolved_from = _valid_iso_date(date_from)
        resolved_to = _valid_iso_date(date_to)
        resolved_sort_by = sort_by if sort_by in SORT_COLUMNS else "detected_date"
        with get_connection(db_path) as conn:
            db_categories = list_categories(conn)
            if q.strip():
                rows = search_documents(conn, q.strip())
                if status != "all":
                    rows = [r for r in rows if r["filing_status"] == status]
                if category:
                    rows = [r for r in rows if r["category"] == category]
                if resolved_from:
                    rows = [r for r in rows if (r["detected_date"] or "") >= resolved_from]
                if resolved_to:
                    rows = [r for r in rows if (r["detected_date"] or "") <= resolved_to]
                rows = _sort_rows(rows, resolved_sort_by, sort_dir)
            else:
                rows = list_documents(conn, status=status, category=category,
                                      date_from=resolved_from, date_to=resolved_to,
                                      sort_by=resolved_sort_by, sort_dir=sort_dir)
        total = len(rows)
        offset = (page - 1) * _PAGE_SIZE
        page_rows = rows[offset: offset + _PAGE_SIZE]
        categories_all = sorted(set(cfg.get("categories", [])) | set(db_categories))
        return {
            "items": [_doc_to_dict(r) for r in page_rows],
            "total": total,
            "page": page,
            "page_size": _PAGE_SIZE,
            "categories": categories_all,
        }

    @api.get("/documents/{doc_id}")
    def api_get_document(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        doc_path = _resolve_db_filepath(row["filepath"], cfg, path_base_from, path_base_to, path_rewrites)
        data = _doc_to_dict(row)
        data["file_exists"] = doc_path.exists()
        return data

    @api.get("/documents/{doc_id}/content")
    def api_document_content(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        doc_path = _resolve_db_filepath(row["filepath"], cfg, path_base_from, path_base_to, path_rewrites)
        if not doc_path.exists() or not doc_path.is_file():
            raise HTTPException(status_code=404, detail="Document file is missing")
        media_type = "application/pdf" if doc_path.suffix.lower() == ".pdf" else "application/octet-stream"
        return FileResponse(path=doc_path, filename=doc_path.name, media_type=media_type)

    @api.get("/documents/{doc_id}/thumbnail")
    def api_document_thumbnail(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        thumb = thumbnail_path(doc_id, cfg)
        if not thumb.exists():
            doc_path = _resolve_db_filepath(row["filepath"], cfg, path_base_from, path_base_to, path_rewrites)
            if not doc_path.exists() or ensure_thumbnail(doc_id, doc_path, cfg) is None:
                raise HTTPException(status_code=404, detail="Thumbnail unavailable")
        return FileResponse(path=thumb, media_type="image/jpeg")

    @api.patch("/documents/{doc_id}")
    def api_patch_document(doc_id: int, patch: DocumentPatch):
        fields_set = patch.model_fields_set
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")

            kwargs: dict = {}
            if "detected_date" in fields_set and patch.detected_date:
                try:
                    date.fromisoformat(patch.detected_date)
                except ValueError:
                    raise HTTPException(status_code=422, detail="detected_date must be YYYY-MM-DD")
                kwargs["detected_date"] = patch.detected_date
            if "category" in fields_set:
                cleaned = (patch.category or "").strip()
                if cleaned:
                    kwargs["category"] = cleaned
                else:
                    kwargs["clear_category"] = True

            if kwargs:
                update_document_fields(conn, doc_id, classification_source="manual", skipped=0, **kwargs)
            row = get_document_by_id(conn, doc_id)
        return _doc_to_dict(row)

    @api.post("/documents/{doc_id}/ask-ai")
    def api_ask_ai(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            ai_cfg = resolve_ai_config(cfg, conn)
            if ai_cfg is None:
                return {"suggestion": None, "error": "No AI profile is active. Activate one in Settings."}
            suggestion = suggest_date_category(
                text=row["extracted_text"] or "",
                filename=row["filename"],
                categories=cfg.get("categories", []),
                ai_cfg=ai_cfg,
            )
        if not suggestion:
            error = getattr(suggest_date_category, "last_error", "") or "AI suggestion unavailable."
            return {"suggestion": None, "error": error}
        return {"suggestion": suggestion, "error": None}

    @api.post("/documents/{doc_id}/apply-ai")
    def api_apply_ai(doc_id: int, payload: ApplyAiRequest):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")

            update_document_fields(
                conn, doc_id,
                detected_date=payload.detected_date or None,
                category=payload.category or None,
                ai_suggested_category=payload.category or None,
                classification_source="ai",
                ai_rationale=payload.rationale or None,
                ai_summary=payload.summary or None,
                extracted_fields=payload.fields or None,
                skipped=0,
            )

            if payload.detected_date:
                try:
                    src = resolve_stored_path(row["filepath"], cfg)
                    if src.exists():
                        doc_date = date.fromisoformat(payload.detected_date)
                        dest = file_document(
                            src,
                            documents_root=cfg["paths"]["documents"],
                            doc_date=doc_date,
                            category=payload.category or row["category"],
                        )
                        update_document_fields(conn, doc_id, filepath=to_stored_path(dest, cfg))
                        ensure_thumbnail(doc_id, dest, cfg)
                except ValueError:
                    pass

            row = get_document_by_id(conn, doc_id)
        return _doc_to_dict(row)

    @api.post("/documents/{doc_id}/refile")
    def api_refile(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            if not row["detected_date"]:
                raise HTTPException(status_code=400, detail="Cannot refile without a detected date")
            src = resolve_stored_path(row["filepath"], cfg)
            if not src.exists():
                raise HTTPException(status_code=400, detail="Source file missing on disk")
            doc_date = date.fromisoformat(row["detected_date"])
            dest = file_document(
                src, documents_root=cfg["paths"]["documents"], doc_date=doc_date, category=row["category"],
            )
            update_document_fields(
                conn, doc_id, filepath=to_stored_path(dest, cfg), filing_status="filed", skipped=0,
            )
            ensure_thumbnail(doc_id, dest, cfg)
            row = get_document_by_id(conn, doc_id)
        return _doc_to_dict(row)

    @api.post("/documents/{doc_id}/skip")
    def api_skip(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            update_document_fields(conn, doc_id, skipped=1)
            row = get_document_by_id(conn, doc_id)
        return _doc_to_dict(row)

    @api.delete("/documents/{doc_id}", status_code=204)
    def api_delete(doc_id: int):
        with get_connection(db_path) as conn:
            row = get_document_by_id(conn, doc_id)
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.commit()
        return None

    @api.post("/upload")
    def api_upload(file: UploadFile = File(...)):
        # Staged in a private temp dir, never the watched inbox folder — writing
        # into `inbox` would race the background watcher, which would also pick
        # up the same file and process it concurrently.
        name = Path(file.filename or "").name
        if not name.lower().endswith(".pdf"):
            return {"status": "not_pdf", "filename": name}

        with get_connection(db_path) as conn:
            with tempfile.TemporaryDirectory() as tmp_dir:
                staged = Path(tmp_dir) / name
                with staged.open("wb") as out:
                    out.write(file.file.read())
                try:
                    result = process_pdf(staged, cfg=cfg, conn=conn)
                except Exception as exc:
                    return {"status": "failed", "filename": name, "message": str(exc)}

        if result["status"] == "filed":
            return {
                "status": "filed",
                "filename": name,
                "doc_id": result["doc_id"],
                "dest": result["dest"],
                "detected_date": result["detected_date"],
                "category": result["category"],
            }
        if result["status"] == "duplicate":
            return {"status": "duplicate", "filename": name, "path": result["path"]}
        return {"status": result["status"], "filename": name}

    @api.post("/documents/bulk-ask-ai")
    def api_bulk_ask_ai(payload: BulkAskAiRequest):
        doc_ids = payload.doc_ids

        def _events():
            with get_connection(db_path) as conn:
                ai_cfg = resolve_ai_config(cfg, conn)
                categories_cfg = cfg.get("categories", [])
                if ai_cfg is None:
                    for doc_id in doc_ids:
                        yield f"data: {json.dumps({'doc_id': doc_id, 'status': 'error', 'error': 'No AI profile is active. Activate one in Settings.'})}\n\n"
                    yield f"data: {json.dumps({'status': 'done'})}\n\n"
                    return
                for doc_id in doc_ids:
                    row = get_document_by_id(conn, doc_id)
                    if not row:
                        yield f"data: {json.dumps({'doc_id': doc_id, 'status': 'error', 'error': 'Document not found'})}\n\n"
                        continue
                    suggestion = suggest_date_category(
                        text=row["extracted_text"] or "",
                        filename=row["filename"],
                        categories=categories_cfg,
                        ai_cfg=ai_cfg,
                    )
                    if not suggestion:
                        error = getattr(suggest_date_category, "last_error", "") or "AI suggestion unavailable."
                        yield f"data: {json.dumps({'doc_id': doc_id, 'status': 'error', 'error': error})}\n\n"
                    else:
                        yield f"data: {json.dumps({'doc_id': doc_id, 'status': 'suggested', 'suggestion': suggestion})}\n\n"
            yield f"data: {json.dumps({'status': 'done'})}\n\n"

        return StreamingResponse(_events(), media_type="text/event-stream")

    @api.get("/categories")
    def api_list_categories():
        configured = get_configured_categories(cfg_path)
        with get_connection(db_path) as conn:
            db_cats = list_categories(conn)
        return {"configured": configured, "db_only": sorted(set(db_cats) - set(configured))}

    @api.post("/categories", status_code=201)
    def api_add_category(payload: CategoryCreate):
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Category name is required")
        added = add_configured_category(cfg_path, name)
        if not added:
            raise HTTPException(status_code=409, detail="Category already exists")
        return {"name": name}

    @api.delete("/categories/{name}", status_code=204)
    def api_remove_category(name: str):
        removed = remove_configured_category(cfg_path, name)
        if not removed:
            raise HTTPException(status_code=404, detail="Category not found")
        return None

    @api.get("/ai-profiles")
    def api_list_ai_profiles():
        with get_connection(db_path) as conn:
            ensure_ai_profiles_seeded(cfg, conn)
            rows = list_ai_profiles(conn)
        return [_profile_to_dict(r) for r in rows]

    @api.post("/ai-profiles", status_code=201)
    def api_create_ai_profile(payload: AiProfileCreate):
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Profile name is required")
        if payload.provider == "custom" and not payload.base_url.strip():
            raise HTTPException(status_code=422, detail="Custom provider requires a Base URL")
        with get_connection(db_path) as conn:
            profile_id = insert_ai_profile(
                conn, name=name, provider=payload.provider, model=payload.model.strip(),
                base_url=payload.base_url.strip(), api_key=payload.api_key.strip(),
                timeout=payload.timeout, max_tokens=payload.max_tokens,
            )
            row = get_ai_profile(conn, profile_id)
        return _profile_to_dict(row)

    @api.patch("/ai-profiles/{profile_id}")
    def api_update_ai_profile(profile_id: int, payload: AiProfileUpdate):
        fields_set = payload.model_fields_set
        with get_connection(db_path) as conn:
            existing = get_ai_profile(conn, profile_id)
            if not existing:
                raise HTTPException(status_code=404, detail="AI profile not found")

            kwargs: dict = {}
            if "name" in fields_set:
                name = (payload.name or "").strip()
                if not name:
                    raise HTTPException(status_code=422, detail="Profile name is required")
                kwargs["name"] = name
            if "provider" in fields_set:
                kwargs["provider"] = payload.provider
            if "model" in fields_set:
                kwargs["model"] = (payload.model or "").strip()
            if "base_url" in fields_set:
                kwargs["base_url"] = (payload.base_url or "").strip()
            if "api_key" in fields_set and (payload.api_key or "").strip():
                kwargs["api_key"] = payload.api_key.strip()
            if "timeout" in fields_set:
                kwargs["timeout"] = payload.timeout
            if "max_tokens" in fields_set:
                kwargs["max_tokens"] = payload.max_tokens

            resolved_provider = kwargs.get("provider", existing["provider"])
            resolved_base_url = kwargs.get("base_url", existing["base_url"])
            if resolved_provider == "custom" and not resolved_base_url.strip():
                raise HTTPException(status_code=422, detail="Custom provider requires a Base URL")

            if kwargs:
                update_ai_profile(conn, profile_id, **kwargs)
            row = get_ai_profile(conn, profile_id)
        return _profile_to_dict(row)

    @api.delete("/ai-profiles/{profile_id}", status_code=204)
    def api_delete_ai_profile(profile_id: int):
        with get_connection(db_path) as conn:
            existing = get_ai_profile(conn, profile_id)
            if not existing:
                raise HTTPException(status_code=404, detail="AI profile not found")
            delete_ai_profile(conn, profile_id)
        return None

    @api.post("/ai-profiles/{profile_id}/activate")
    def api_activate_ai_profile(profile_id: int):
        with get_connection(db_path) as conn:
            existing = get_ai_profile(conn, profile_id)
            if not existing:
                raise HTTPException(status_code=404, detail="AI profile not found")
            set_active_ai_profile(conn, profile_id)
            row = get_ai_profile(conn, profile_id)
        return _profile_to_dict(row)

    @api.post("/ai-profiles/test")
    def api_test_ai_profile_draft(payload: AiProfileTestPayload):
        if payload.provider == "custom" and not payload.base_url.strip():
            return {"ok": False, "message": "Custom provider requires a Base URL."}
        ok, message = test_provider_connection({
            "provider": payload.provider, "model": payload.model, "base_url": payload.base_url,
            "api_key": payload.api_key, "timeout": payload.timeout, "max_tokens": payload.max_tokens,
        })
        return {"ok": ok, "message": message}

    @api.post("/ai-profiles/{profile_id}/test")
    def api_test_ai_profile(profile_id: int, payload: AiProfileTestPayload):
        with get_connection(db_path) as conn:
            existing = get_ai_profile(conn, profile_id)
        if not existing:
            raise HTTPException(status_code=404, detail="AI profile not found")
        if payload.provider == "custom" and not payload.base_url.strip():
            return {"ok": False, "message": "Custom provider requires a Base URL."}
        api_key = payload.api_key.strip() or existing["api_key"]
        ok, message = test_provider_connection({
            "provider": payload.provider, "model": payload.model, "base_url": payload.base_url,
            "api_key": api_key, "timeout": payload.timeout, "max_tokens": payload.max_tokens,
        })
        return {"ok": ok, "message": message}

    @api.get("/meta")
    def api_meta():
        return {"provider_defaults": PROVIDER_DEFAULTS, "default_settings": DEFAULT_AI_SETTINGS}

    app.include_router(api)

    # Built frontend assets (present in the Docker image after the Node build
    # stage; absent in local dev, where the Vite dev server is used instead).
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        index_file = static_dir / "index.html"

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found")
            candidate = static_dir / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_file)

    return app

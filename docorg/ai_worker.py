"""Background AI summarisation: retries documents that have no AI result yet.

Runs next to the inbox watcher. Each pass picks documents that still "need AI",
asks the pool of enabled profiles (same path as /ask-ai), then either applies the
suggestion or stores it as pending for review, depending on the auto-apply setting.
Failures back off exponentially and are capped by a max-attempts counter.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone

from .ai import resolve_ai_profiles, run_suggestion_pool
from .database import (
    get_connection,
    get_ai_queue_settings,
    get_document_by_id,
    list_docs_needing_ai,
    record_ai_failure,
    store_pending_ai_suggestion,
    update_document_fields,
)
from .filer import file_document
from .pathing import resolve_stored_path, to_stored_path
from .thumbnail import ensure_thumbnail

POLL_SECONDS = 30
BACKOFF_BASE_SECONDS = 300      # 5 min, doubling per failed attempt
BACKOFF_MAX_SECONDS = 6 * 3600


def backoff_seconds(attempts: int) -> int:
    """Delay before the next try, given how many attempts have already failed."""
    return min(BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0)), BACKOFF_MAX_SECONDS)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def apply_ai_suggestion(conn, cfg: dict, row, fields: dict, *, model: str | None = None):
    """Store an AI result on a document and refile it by date/category.

    `fields` uses the suggestion shape (date, category, rationale, summary, fields,
    primary_person, ...). Shared by the manual apply endpoint, the pending-review
    flow and the background worker. Returns the refreshed row.
    """
    doc_id = row["id"]
    detected_date = fields.get("date") or fields.get("detected_date") or None
    category = fields.get("category") or None
    update_document_fields(
        conn, doc_id,
        detected_date=detected_date,
        category=category,
        ai_suggested_category=category,
        classification_source="ai",
        ai_rationale=fields.get("rationale") or None,
        ai_summary=fields.get("summary") or None,
        extracted_fields=fields.get("fields") or None,
        primary_person=fields.get("primary_person") or None,
        issuing_organization=fields.get("issuing_organization") or None,
        reference_number=fields.get("reference_number") or None,
        amount=fields.get("amount") or None,
        effective_date=fields.get("effective_date") or None,
        expiry_date=fields.get("expiry_date") or None,
        ai_model=model or None,
        clear_ai_queue_state=True,
        skipped=0,
    )

    if detected_date:
        try:
            src = resolve_stored_path(row["filepath"], cfg)
            if src.exists():
                dest = file_document(
                    src,
                    documents_root=cfg["paths"]["documents"],
                    doc_date=date.fromisoformat(detected_date),
                    category=category or row["category"],
                )
                update_document_fields(conn, doc_id, filepath=to_stored_path(dest, cfg))
                ensure_thumbnail(doc_id, dest, cfg)
        except ValueError:
            pass
    return get_document_by_id(conn, doc_id)


def process_document(conn, cfg: dict, row, *, profiles: list[dict], settings: dict) -> str:
    """One AI attempt for one document. Returns "applied", "pending" or "failed"."""
    suggestion, error, model = run_suggestion_pool(
        text=row["extracted_text"] or "",
        filename=row["filename"],
        categories=cfg.get("categories", []),
        profiles=profiles,
        mode=settings["mode"],
    )
    if suggestion is None:
        delay = backoff_seconds(row["ai_attempts"] + 1)
        next_at = _iso(datetime.now(timezone.utc) + timedelta(seconds=delay))
        record_ai_failure(conn, row["id"], error, next_attempt_at=next_at)
        return "failed"

    # Don't clobber a manual classification silently: park it for review instead.
    manual = row["classification_source"] == "manual"
    if settings["auto_apply"] and not manual:
        apply_ai_suggestion(conn, cfg, row, suggestion, model=model)
        return "applied"
    store_pending_ai_suggestion(conn, row["id"], suggestion, model)
    return "pending"


class AiWorker:
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._db_path = cfg["paths"]["database"]
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="docorg-ai-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=5)

    def wake(self) -> None:
        """Run a pass now instead of waiting for the next poll."""
        self._wake.set()

    def run_pass(self) -> int:
        """Process one batch of due documents. Returns how many were attempted."""
        with get_connection(self._db_path) as conn:
            settings = get_ai_queue_settings(conn)
            if not settings["background_enabled"]:
                return 0
            profiles = resolve_ai_profiles(self._cfg, conn)
            if not profiles:
                return 0
            rows = list_docs_needing_ai(conn, max_attempts=settings["max_attempts"], limit=5)
            attempted = 0
            for stale in rows:
                if self._stop.is_set():
                    break
                # Re-read: the user may have archived/summarised it since we listed it.
                row = get_document_by_id(conn, stale["id"])
                if row is None or row["archived_at"] or row["ai_summary"] or row["ai_pending_suggestion"]:
                    continue
                try:
                    process_document(conn, self._cfg, row, profiles=profiles, settings=settings)
                except Exception as exc:  # never let one document kill the worker
                    record_ai_failure(
                        conn, row["id"], f"Unexpected error: {exc}",
                        next_attempt_at=_iso(datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds(row["ai_attempts"] + 1))),
                    )
                attempted += 1
            return attempted

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                attempted = self.run_pass()
            except Exception as exc:
                print(f"[ai-worker] pass failed: {exc}")
                attempted = 0
            if attempted:
                continue  # more may be due; loop immediately
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()


def pending_suggestion(row) -> dict | None:
    raw = row["ai_pending_suggestion"]
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None

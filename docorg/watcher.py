import time
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .database import get_connection
from .processor import process_pdf


def _is_retryable_file_error(exc: Exception) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        err_no = getattr(exc, "errno", None)
        win_err = getattr(exc, "winerror", None)
        return err_no in {13, 16} or win_err in {32, 33}
    return False


class _PDFHandler(FileSystemEventHandler):
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._db_path = cfg["paths"]["database"]

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".pdf":
            return

        watch_cfg = self._cfg.get("watch", {})
        max_retries = int(watch_cfg.get("max_retries", 8))
        retry_delay_seconds = float(watch_cfg.get("retry_delay_seconds", 0.5))
        retry_backoff = float(watch_cfg.get("retry_backoff", 1.5))
        retry_max_delay_seconds = float(watch_cfg.get("retry_max_delay_seconds", 3.0))

        delay = retry_delay_seconds
        result = None

        for attempt in range(1, max_retries + 1):
            try:
                with get_connection(self._db_path) as conn:
                    result = process_pdf(path, cfg=self._cfg, conn=conn)
                break
            except Exception as exc:  # keep watchdog thread alive on processing failures
                if _is_retryable_file_error(exc) and attempt < max_retries:
                    time.sleep(delay)
                    delay = min(delay * retry_backoff, retry_max_delay_seconds)
                    continue

                print(f"[error]     Failed to process {path}: {exc}")
                return

        if result is None:
            return

        if result["status"] == "filed":
            print(
                f"[filed]     {result['filename']}\n"
                f"            -> {result['dest']}\n"
                f"            date={result['detected_date'] or '(fallback)'}  "
                f"category={result['category'] or '(none)'}  "
                f"source={result['classification_source']}"
            )
        elif result["status"] == "duplicate":
            print(f"[duplicate] {result['path']} — already in database, skipped.")


def start_watcher(cfg: dict) -> None:
    inbox = cfg["paths"]["inbox"]
    Path(inbox).mkdir(parents=True, exist_ok=True)

    handler = _PDFHandler(cfg)
    observer = Observer()
    observer.schedule(handler, path=inbox, recursive=False)
    observer.start()

    print(f"Watching {inbox}  (Ctrl+C to stop)")
    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

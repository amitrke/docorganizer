import time
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .database import get_connection
from .processor import process_pdf


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

        # Brief pause to ensure the file is fully written before we open it
        time.sleep(0.5)

        with get_connection(self._db_path) as conn:
            result = process_pdf(path, cfg=self._cfg, conn=conn)

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

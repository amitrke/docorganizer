import unittest
from contextlib import nullcontext
from unittest.mock import patch

from watchdog.events import FileCreatedEvent

from docorg.watcher import _PDFHandler


class WatcherTests(unittest.TestCase):
    def test_created_pdf_retries_permission_error_then_succeeds(self):
        cfg = {
            "paths": {"database": "docorganizer.db"},
            "watch": {
                "max_retries": 3,
                "retry_delay_seconds": 0.01,
                "retry_backoff": 1.0,
                "retry_max_delay_seconds": 0.01,
            },
        }
        handler = _PDFHandler(cfg)
        event = FileCreatedEvent("Y:/docsorg/inbox/locked.pdf")

        side_effect = [
            PermissionError("locked"),
            PermissionError("locked"),
            {"status": "duplicate", "path": event.src_path},
        ]

        with patch("docorg.watcher.get_connection", return_value=nullcontext(object())):
            with patch("docorg.watcher.process_pdf", side_effect=side_effect) as process_mock:
                with patch("docorg.watcher.time.sleep") as sleep_mock:
                    handler.on_created(event)

        self.assertEqual(process_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()

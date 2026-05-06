import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from docorg.database import get_connection, init_db
from docorg.processor import analyze_pdf, process_pdf


class HashDedupTests(unittest.TestCase):
    def test_same_content_different_paths_is_duplicate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            inbox = root / "scans"
            docs = root / "documents"
            db_path = root / "docorganizer.db"
            inbox.mkdir(parents=True, exist_ok=True)
            docs.mkdir(parents=True, exist_ok=True)

            first = inbox / "a.pdf"
            second = inbox / "b.pdf"
            payload = b"%PDF-1.4\nSAME_CONTENT\n%%EOF\n"
            first.write_bytes(payload)
            second.write_bytes(payload)

            cfg = {
                "paths": {
                    "inbox": str(inbox),
                    "documents": str(docs),
                    "database": str(db_path),
                },
                "rules": [],
                "date_detection": {},
            }

            init_db(db_path)
            conn = get_connection(db_path)
            try:
                with patch("docorg.processor.extract_text", return_value="text"):
                    with patch("docorg.processor.detect_date", return_value=(date(2026, 5, 1), 1)):
                        first_result = process_pdf(first, cfg=cfg, conn=conn, skip=True)
                        self.assertEqual(first_result["status"], "skipped")

                second_result = analyze_pdf(second, cfg=cfg, conn=conn)
                self.assertEqual(second_result["status"], "duplicate")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from datetime import date, datetime

from docorg.date_detector import detect_date


class DateDetectorTests(unittest.TestCase):
    def test_filename_mdy_date_takes_priority(self):
        d, count = detect_date(
            "Previous visit was 27/10/2024.",
            file_path="scans/04292026_Your medications have changed.pdf",
        )
        self.assertEqual(d, date(2026, 4, 29))
        self.assertEqual(count, 1)

    def test_keyword_prefixed_date_wins_over_other_text_dates(self):
        text = (
            "Previous visit: 27/10/2024\n"
            "Statement Date: 29/04/2026\n"
            "Follow up in 2027-01-01"
        )
        d, count = detect_date(text)
        self.assertEqual(d, date(2026, 4, 29))
        self.assertEqual(count, 1)

    def test_custom_keyword_list_is_used(self):
        text = "Document Created On 2026-05-03\nPrevious date 2024-10-27"
        d, count = detect_date(
            text,
            date_keywords=["document created on"],
        )
        self.assertEqual(d, date(2026, 5, 3))
        self.assertEqual(count, 1)

    def test_generic_text_date_still_detected(self):
        d, count = detect_date("Lab report generated on 15/03/2025")
        self.assertEqual(d, date(2025, 3, 15))
        self.assertEqual(count, 1)

    def test_mtime_fallback_when_no_dates(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            path = tmp.name
        try:
            ts = 1714521600  # 2024-05-01 UTC
            os.utime(path, (ts, ts))
            d, count = detect_date("No date present here", file_path=path)
            self.assertEqual(d, datetime.fromtimestamp(ts).date())
            self.assertEqual(count, 0)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()

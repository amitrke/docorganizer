import types
import unittest
from unittest.mock import patch

from docorg import extractor


class _FakeDoc:
    def __init__(self, pages):
        self._pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self._pages)


class _FakePage:
    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text


class ExtractorTests(unittest.TestCase):
    def test_extract_text_uses_selectable_text(self):
        fake_fitz = types.SimpleNamespace(
            open=lambda _path: _FakeDoc([_FakePage("hello"), _FakePage("world")])
        )

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            with patch("docorg.extractor._ocr_page_text") as ocr_mock:
                text = extractor.extract_text("dummy.pdf")

        self.assertEqual(text, "hello\nworld")
        ocr_mock.assert_not_called()

    def test_extract_text_falls_back_to_ocr(self):
        fake_fitz = types.SimpleNamespace(
            open=lambda _path: _FakeDoc([_FakePage("   "), _FakePage("machine text")])
        )

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            with patch("docorg.extractor._ocr_page_text", return_value="ocr text") as ocr_mock:
                text = extractor.extract_text("dummy.pdf")

        self.assertEqual(text, "machine text\nocr text")
        ocr_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()

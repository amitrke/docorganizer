import json
import unittest
from unittest.mock import patch

from docorg.ai import suggest_date_category


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class AiParsingTests(unittest.TestCase):
    def test_truncated_model_json_is_repaired(self):
        model_response = (
            '{"date":"2022-10-30","category":"education","rationale":"The document is '
            'a school offer for special education services, specifically an Individualized '
            'Education Program (IEP), which falls'
        )
        payload = {"response": model_response}

        with patch("docorg.ai.request.urlopen", return_value=_FakeResponse(payload)):
            suggestion = suggest_date_category(
                text="irrelevant",
                filename="iep.pdf",
                categories=["education", "health"],
                ai_cfg={"enabled": True, "model": "test-model", "timeout": 1, "max_tokens": 32},
            )

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["date"], "2022-10-30")
        self.assertEqual(suggestion["category"], "education")
        self.assertTrue(suggestion["rationale"].startswith("The document is a school offer"))
        self.assertEqual(suggestion["summary"], "")
        self.assertEqual(suggestion["fields"], {})


if __name__ == "__main__":
    unittest.main()
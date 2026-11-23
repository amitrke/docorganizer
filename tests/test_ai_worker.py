import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docorg import ai
from docorg.ai_worker import AiWorker, backoff_seconds
from docorg.database import (
    ai_queue_counts,
    get_connection,
    get_document_by_id,
    init_db,
    insert_ai_profile,
    insert_document,
    list_enabled_ai_profiles,
    reorder_ai_profiles,
    set_ai_profile_enabled,
    set_ai_queue_settings,
)

CATS = ["education", "health"]


def _good(category="education", date="2022-10-30"):
    return ({"date": date, "category": category, "rationale": "r", "summary": "s", "fields": {},
             "primary_person": None, "issuing_organization": None, "reference_number": None,
             "amount": None, "effective_date": None, "expiry_date": None}, "")


def _profiles(n):
    return [{"id": i, "name": f"p{i}", "provider": "custom", "model": f"m{i}"} for i in range(1, n + 1)]


class PoolTests(unittest.TestCase):
    def run_pool(self, results, mode="fallback", n=None):
        it = iter(results)
        with patch("docorg.ai.request_suggestion", side_effect=lambda **kw: next(it)):
            return ai.run_suggestion_pool(
                text="t", filename="f.pdf", categories=CATS,
                profiles=_profiles(n or len(results)), mode=mode,
            )

    def test_fallback_uses_next_profile_after_failure(self):
        suggestion, error, model = self.run_pool([(None, "boom"), _good()])
        self.assertEqual(suggestion["category"], "education")
        self.assertIn("p2", model)

    def test_empty_category_is_rejected_and_falls_through(self):
        suggestion, error, _ = self.run_pool([_good(category=None), _good("health")])
        self.assertEqual(suggestion["category"], "health")

    def test_unknown_category_everywhere_returns_error(self):
        suggestion, error, _ = self.run_pool([_good("weather"), _good(category="")])
        self.assertIsNone(suggestion)
        self.assertIn("p1", error)

    def test_invalid_date_is_dropped_not_applied(self):
        suggestion, _, _ = self.run_pool([_good(date="not-a-date")])
        self.assertIsNone(suggestion["date"])

    def test_consensus_requires_agreement(self):
        suggestion, _, model = self.run_pool([_good("education"), _good("health"), _good("education")], mode="consensus")
        self.assertEqual(suggestion["category"], "education")
        self.assertIn("+", model)
        suggestion, error, _ = self.run_pool([_good("education"), _good("health")], mode="consensus")
        self.assertIsNone(suggestion)
        self.assertIn("agreed", error)

    def test_round_robin_rotates_start(self):
        calls = []

        def fake(**kw):
            calls.append(kw["ai_cfg"]["name"])
            return _good()

        ai._rr_counter = 0
        with patch("docorg.ai.request_suggestion", side_effect=fake):
            for _ in range(3):
                ai.run_suggestion_pool(text="t", filename="f", categories=CATS,
                                       profiles=_profiles(3), mode="round_robin")
        self.assertEqual(calls, ["p1", "p2", "p3"])

    def test_no_profiles(self):
        suggestion, error, _ = ai.run_suggestion_pool(
            text="t", filename="f", categories=CATS, profiles=[], mode="fallback")
        self.assertIsNone(suggestion)
        self.assertIn("enabled", error)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        (root / "documents").mkdir()
        self.cfg = {"paths": {"database": str(root / "t.db"), "documents": str(root / "documents"),
                              "inbox": str(root / "inbox")}, "categories": CATS}
        init_db(self.cfg["paths"]["database"])
        with get_connection(self.cfg["paths"]["database"]) as conn:
            for name in ("a", "b"):
                insert_ai_profile(conn, name=name, provider="custom", model=name, base_url="http://x",
                                  api_key="", timeout=5, max_tokens=100, is_active=True)
            self.doc_id = insert_document(
                conn, filename="x.pdf", filepath="documents/x.pdf", extracted_text="hello",
                detected_date=None, category=None, filing_status="filed")

    def tearDown(self):
        self.tmp.cleanup()

    def row(self):
        with get_connection(self.cfg["paths"]["database"]) as conn:
            return get_document_by_id(conn, self.doc_id)

    def test_failure_records_error_and_backs_off(self):
        with patch("docorg.ai.request_suggestion", return_value=(None, "down")):
            self.assertEqual(AiWorker(self.cfg).run_pass(), 1)
            self.assertEqual(AiWorker(self.cfg).run_pass(), 0)  # backed off
        row = self.row()
        self.assertEqual(row["ai_attempts"], 1)
        self.assertIn("down", row["ai_last_error"])

    def test_stops_after_max_attempts(self):
        with get_connection(self.cfg["paths"]["database"]) as conn:
            set_ai_queue_settings(conn, max_attempts=1)
        with patch("docorg.ai.request_suggestion", return_value=(None, "down")):
            AiWorker(self.cfg).run_pass()
            with get_connection(self.cfg["paths"]["database"]) as conn:
                conn.execute("UPDATE documents SET ai_next_attempt_at = NULL")
                conn.commit()
                self.assertEqual(ai_queue_counts(conn, max_attempts=1)["exhausted"], 1)
            self.assertEqual(AiWorker(self.cfg).run_pass(), 0)

    def test_default_stores_pending_without_changing_document(self):
        with patch("docorg.ai.request_suggestion", return_value=_good()):
            AiWorker(self.cfg).run_pass()
        row = self.row()
        self.assertIsNotNone(row["ai_pending_suggestion"])
        self.assertIsNone(row["category"])
        self.assertIn("a", row["ai_model"])

    def test_auto_apply_sets_category_and_model(self):
        with get_connection(self.cfg["paths"]["database"]) as conn:
            set_ai_queue_settings(conn, auto_apply=True)
        with patch("docorg.ai.request_suggestion", return_value=_good()):
            AiWorker(self.cfg).run_pass()
        row = self.row()
        self.assertEqual(row["category"], "education")
        self.assertEqual(row["ai_summary"], "s")
        self.assertEqual(row["classification_source"], "ai")
        self.assertIsNone(row["ai_pending_suggestion"])

    def test_invalid_suggestion_never_applied(self):
        with get_connection(self.cfg["paths"]["database"]) as conn:
            set_ai_queue_settings(conn, auto_apply=True)
        with patch("docorg.ai.request_suggestion", return_value=_good(category="")):
            AiWorker(self.cfg).run_pass()
        row = self.row()
        self.assertIsNone(row["category"])
        self.assertEqual(row["ai_attempts"], 1)

    def test_profile_priority_and_enable(self):
        with get_connection(self.cfg["paths"]["database"]) as conn:
            ids = [p["id"] for p in list_enabled_ai_profiles(conn)]
            reorder_ai_profiles(conn, list(reversed(ids)))
            self.assertEqual([p["id"] for p in list_enabled_ai_profiles(conn)], list(reversed(ids)))
            set_ai_profile_enabled(conn, ids[0], False)
            self.assertEqual(len(list_enabled_ai_profiles(conn)), 1)

    def test_backoff_grows_and_caps(self):
        self.assertLess(backoff_seconds(1), backoff_seconds(2))
        self.assertEqual(backoff_seconds(50), backoff_seconds(51))


if __name__ == "__main__":
    unittest.main()

import unittest

from docorg.processor import _match_category


class RuleScoringTests(unittest.TestCase):
    def test_legacy_keywords_rule_still_works(self):
        rules = [
            {"category": "tax", "keywords": ["form 1040"], "priority": 10},
            {"category": "health", "keywords": ["clinic"], "priority": 10},
        ]

        category = _match_category(
            text="This packet includes form 1040 and schedule notes.",
            filename="return.pdf",
            rules=rules,
        )

        self.assertEqual(category, "tax")

    def test_exclude_keywords_prevent_collision(self):
        rules = [
            {
                "category": "tax",
                "keywords": ["schedule"],
                "exclude_keywords": ["public schools"],
                "priority": 10,
            },
            {
                "category": "education",
                "keywords": ["public schools"],
                "priority": 10,
            },
        ]

        category = _match_category(
            text="Henrico County Public Schools schedule update for student services.",
            filename="school_notice.pdf",
            rules=rules,
        )

        self.assertEqual(category, "education")

    def test_ambiguous_scores_return_none(self):
        rules = [
            {"category": "tax", "keywords": ["statement"], "priority": 10},
            {"category": "finance", "keywords": ["statement"], "priority": 10},
        ]

        category = _match_category(
            text="Account statement for period ending April.",
            filename="statement.pdf",
            rules=rules,
            classifier_cfg={"min_score_gap": 1.0},
        )

        self.assertIsNone(category)

    def test_filename_keywords_are_weighted(self):
        rules = [
            {
                "category": "immigration",
                "filename_keywords": ["h1b"],
                "keywords": ["visa"],
                "priority": 10,
            },
            {"category": "tax", "keywords": ["visa"], "priority": 10},
        ]

        category = _match_category(
            text="Visa processing instructions enclosed.",
            filename="Amit-H1B-2022-Infy.pdf",
            rules=rules,
        )

        self.assertEqual(category, "immigration")


if __name__ == "__main__":
    unittest.main()

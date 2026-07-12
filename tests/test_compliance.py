import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoreel.compliance import ComplianceEngine


def word(w, start, end):
    return {"word": w, "start": start, "end": end}


class ComplianceEngineTests(unittest.TestCase):
    def test_flags_custom_words(self):
        engine = ComplianceEngine(custom_words=("brandx",))
        words = [word("hello", 0.0, 0.5), word("BrandX", 0.5, 1.0), word("world", 1.0, 1.5)]

        violations = engine.scan_words(words)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].word, "BrandX")
        self.assertEqual(violations[0].category, "custom_word")

    def test_flags_sensitive_categories(self):
        engine = ComplianceEngine()
        words = [word("heroin", 2.0, 2.4), word("suicide", 5.0, 5.6)]

        violations = engine.scan_words(words)

        categories = {v.category for v in violations}
        self.assertIn("drugs", categories)
        self.assertIn("self_harm", categories)

    def test_clean_transcript_has_no_violations(self):
        engine = ComplianceEngine()
        words = [word("hello", 0.0, 0.5), word("world", 0.5, 1.0)]

        violations = engine.scan_words(words)

        self.assertEqual(violations, [])
        self.assertTrue(engine.is_kid_friendly(violations))

    def test_scan_segments_delegates_to_scan_words(self):
        engine = ComplianceEngine(custom_words=("acme",))
        segments = [
            {"words": [word("acme", 0.0, 0.3)]},
            {"words": [word("clean", 0.3, 0.6)]},
        ]

        violations = engine.scan_segments(segments)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].word, "acme")

    def test_extra_categories_are_merged(self):
        engine = ComplianceEngine(extra_categories={"spoilers": ["series finale reveal"]})
        words = [word("series finale reveal", 0.0, 1.0)]

        violations = engine.scan_words(words)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].category, "spoilers")

    def test_punctuation_is_stripped_before_matching(self):
        engine = ComplianceEngine(custom_words=("acme",))
        violations = engine.scan_words([word("Acme!", 0.0, 0.4)])

        self.assertEqual(len(violations), 1)


if __name__ == "__main__":
    unittest.main()

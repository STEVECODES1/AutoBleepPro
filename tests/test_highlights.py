import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoreel.highlights import HighlightScorer


def segment(start, end, text, words=None):
    return {"start": start, "end": end, "text": text, "words": words or []}


class HighlightScorerTests(unittest.TestCase):
    def test_scores_exciting_segments_higher(self):
        scorer = HighlightScorer()
        exciting = segment(0, 5, "This is INSANE, no way, let's go!!!")
        boring = segment(5, 10, "So then we went to the store.")

        exciting_score = scorer.score_segment(exciting)
        boring_score = scorer.score_segment(boring)

        self.assertGreater(exciting_score, boring_score)

    def test_select_clips_returns_requested_count(self):
        scorer = HighlightScorer(min_duration=5, max_duration=20)
        segments = [
            segment(0, 6, "That was insane, wow!"),
            segment(10, 16, "Nothing much happening here."),
            segment(30, 36, "Unbelievable, incredible, amazing!"),
            segment(60, 66, "Just walking around, normal stuff."),
        ]

        clips = scorer.select_clips(segments, count=2)

        self.assertEqual(len(clips), 2)
        # Highest scoring segments should be the ones chosen.
        chosen_windows = {(c.start, c.end) for c in clips}
        self.assertTrue(any(w[0] <= 0 for w in chosen_windows))
        self.assertTrue(any(w[0] <= 30 for w in chosen_windows))

    def test_select_clips_avoids_overlap(self):
        scorer = HighlightScorer(min_duration=5, max_duration=20)
        segments = [
            segment(0, 5, "insane insane insane!"),
            segment(4, 9, "insane insane insane!"),
        ]

        clips = scorer.select_clips(segments, count=2, min_gap=2.0)

        # The second candidate overlaps the first within min_gap, so only
        # one clip should be selected.
        self.assertEqual(len(clips), 1)

    def test_select_clips_pads_short_segments_to_min_duration(self):
        scorer = HighlightScorer(min_duration=15, max_duration=60)
        segments = [
            segment(0, 3, "setup context here"),
            segment(3, 6, "insane! wow! unbelievable!"),
            segment(6, 10, "more context after"),
        ]

        clips = scorer.select_clips(segments, count=1)

        self.assertEqual(len(clips), 1)
        self.assertGreaterEqual(clips[0].end - clips[0].start, 6)

    def test_no_positive_score_segments_returns_empty(self):
        scorer = HighlightScorer()
        segments = [segment(0, 5, "quiet plain sentence")]

        clips = scorer.select_clips(segments, count=3)

        self.assertEqual(clips, [])

    def test_streaming_reaction_phrases_score_higher(self):
        scorer = HighlightScorer()
        reaction = segment(0, 5, "Hahaha no shot, chat clip that!")
        boring = segment(5, 10, "So then we went to the store.")

        self.assertGreater(scorer.score_segment(reaction), scorer.score_segment(boring))

    def test_all_caps_shouting_scores_higher_than_lowercase(self):
        scorer = HighlightScorer()
        shouted = segment(0, 5, "THIS IS ACTUALLY HAPPENING RIGHT NOW")
        calm = segment(5, 10, "this is actually happening right now")

        self.assertGreater(scorer.score_segment(shouted), scorer.score_segment(calm))

    def test_elongated_words_score_higher_than_normal_form(self):
        scorer = HighlightScorer()
        elongated = segment(0, 5, "noooooo not like that")
        normal = segment(5, 10, "no not like that")

        self.assertGreater(scorer.score_segment(elongated), scorer.score_segment(normal))


if __name__ == "__main__":
    unittest.main()

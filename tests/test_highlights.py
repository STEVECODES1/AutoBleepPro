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


class WindowQualityTests(unittest.TestCase):
    """The gates that stop a clip being ten seconds of nothing."""

    def run_of(self, start, count, text, length=4.0, gap=0.2, vary=True):
        """Consecutive segments. `vary` keeps them distinct, because
        identical repeated lines are what the loop detector rejects."""
        segments, at = [], start
        for n in range(count):
            line = f"{text} number {n}" if vary else text
            segments.append(segment(at, at + length, line))
            at += length + gap
        return segments

    def test_dead_air_window_is_rejected(self):
        """One shout with twenty seconds of silence around it is not a clip."""
        scorer = HighlightScorer(min_duration=15, max_duration=60)
        segments = [
            segment(0.0, 2.0, "This is INSANE, no way!!!"),
            segment(28.0, 30.0, "yeah"),
        ]
        self.assertEqual(scorer.select_clips(segments, count=3), [])

    def test_a_gap_mid_window_ends_the_moment(self):
        scorer = HighlightScorer(min_duration=10, max_duration=60,
                                 min_speech_ratio=0.0)
        segments = [
            segment(0.0, 5.0, "Insane, no way, unbelievable!"),
            # Ten seconds of nothing: whatever comes next is a new moment.
            segment(15.0, 20.0, "Insane, no way, unbelievable!"),
        ]
        for clip in scorer.select_clips(segments, count=3):
            self.assertLess(clip.end - clip.start, 12,
                            "a window was stretched across the silence")

    def test_whisper_repeating_itself_is_not_a_highlight(self):
        """Whisper loops on music; five identical lines is a bug, not a moment."""
        scorer = HighlightScorer(min_duration=10, max_duration=60)
        looped = self.run_of(0.0, 6, "Oh my god, oh my god!", vary=False)
        self.assertEqual(scorer.select_clips(looped, count=3), [])

    def test_sustained_excitement_beats_one_spike_in_a_long_window(self):
        scorer = HighlightScorer(min_duration=15, max_duration=60)
        sustained = self.run_of(0.0, 5, "No way, that was actually insane!")
        # Same length, one good line, the rest filler.
        quiet = self.run_of(200.0, 5, "and then we walked over there")
        quiet[2] = segment(quiet[2]["start"], quiet[2]["end"],
                           "No way, that was actually insane!")

        clips = scorer.select_clips(sustained + quiet, count=1)

        self.assertEqual(len(clips), 1)
        self.assertLess(clips[0].start, 100, "picked the quiet stretch")

    def test_the_payoff_is_not_the_first_thing_in_the_clip(self):
        scorer = HighlightScorer(min_duration=15, max_duration=40)
        segments = self.run_of(0.0, 10, "so we are just walking here now")
        # The peak lands 30s in, with room either side to choose from.
        segments[7] = segment(segments[7]["start"], segments[7]["end"],
                              "OH MY GOD no way, that was actually insane!!!")

        clips = scorer.select_clips(segments, count=1)

        self.assertEqual(len(clips), 1)
        peak_start, peak_end = segments[7]["start"], segments[7]["end"]
        # Build-up before it and enough after it to land: the old scorer
        # started the clip ON the peak and padded forwards, so the payoff
        # was the first thing a viewer heard.
        self.assertLess(clips[0].start, peak_start - 3,
                        "the clip opens on the punchline instead of building to it")
        self.assertGreater(clips[0].end, peak_end + 3,
                           "the clip cuts off on the punchline")

    def test_intro_is_skipped_when_asked(self):
        scorer = HighlightScorer(min_duration=10, max_duration=60,
                                 skip_intro_seconds=60)
        starting_soon = self.run_of(0.0, 4, "No way, this is actually insane!")
        later = self.run_of(300.0, 4, "No way, this is unbelievable, wow!")

        clips = scorer.select_clips(starting_soon + later, count=3)

        self.assertTrue(clips)
        self.assertTrue(all(c.start >= 60 for c in clips))

    def test_clips_do_not_all_come_from_one_good_minute(self):
        scorer = HighlightScorer(min_duration=15, max_duration=60)
        segments = []
        for minute in range(6):
            segments.extend(self.run_of(minute * 300.0, 6,
                                        "No way, that was actually insane!"))

        clips = scorer.select_clips(segments, count=5, min_gap=90)

        self.assertEqual(len(clips), 5)
        starts = sorted(c.start for c in clips)
        for earlier, later in zip(starts, starts[1:]):
            self.assertGreaterEqual(later - earlier, 90)


class HookTests(unittest.TestCase):
    """The title comes off the clip, so it has to read like one."""

    def test_a_complete_sentence_beats_a_fragment(self):
        scorer = HighlightScorer()
        self.assertEqual(
            scorer.best_line(["yo. That monkey just stole the whole bank!"]),
            "That monkey just stole the whole bank!")

    def test_two_words_is_never_the_title(self):
        scorer = HighlightScorer()
        chosen = scorer.best_line(["Wow! He actually pulled that off first try."])
        self.assertNotEqual(chosen, "Wow!")

    def test_a_selected_clip_carries_a_hook(self):
        scorer = HighlightScorer(min_duration=10, max_duration=60)
        segments = [
            segment(0.0, 4.0, "so anyway we were just standing around."),
            segment(4.2, 8.0, "Bro this guy just walked straight into the water!"),
            segment(8.2, 12.0, "OH MY GOD that is actually insane!!!"),
        ]

        clips = scorer.select_clips(segments, count=1)

        self.assertEqual(len(clips), 1)
        self.assertTrue(clips[0].hook)
        self.assertNotIn("so anyway", clips[0].hook.lower())


if __name__ == "__main__":
    unittest.main()

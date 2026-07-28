import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoreel.compliance import Violation
from autoreel.highlights import Highlight
from autoreel.pipeline import AutoReelPipeline, SupervisorReport


class SupervisorReportTests(unittest.TestCase):
    def test_kid_friendly_when_no_violations(self):
        report = SupervisorReport(
            source_path="stream.mp4", violations=[], clips=[], clip_paths=[]
        )

        self.assertTrue(report.is_kid_friendly)
        self.assertIn("PASS", report.to_markdown())

    def test_fails_kid_friendly_when_violations_present(self):
        report = SupervisorReport(
            source_path="stream.mp4",
            violations=[Violation(word="darn", start=1.0, end=1.5, category="profanity")],
            clips=[],
            clip_paths=[],
        )

        self.assertFalse(report.is_kid_friendly)
        markdown = report.to_markdown()
        self.assertIn("FAIL", markdown)
        self.assertIn("darn", markdown)
        self.assertIn("profanity", markdown)

    def test_lists_generated_clips(self):
        report = SupervisorReport(
            source_path="stream.mp4",
            violations=[],
            clips=[Highlight(start=10, end=40, score=9.5, text="Insane play!")],
            clip_paths=["out/stream_01.mp4"],
        )

        markdown = report.to_markdown()

        self.assertIn("stream_01.mp4", markdown)
        self.assertIn("Insane play!", markdown)
        self.assertIn("9.5", markdown)


class AutoReelPipelineWiringTests(unittest.TestCase):
    def test_components_receive_configured_options(self):
        pipeline = AutoReelPipeline(
            output_dir="out",
            model_name="small",
            bleep_method="silence",
            custom_words=("acme",),
            num_clips=5,
            clip_min_duration=20.0,
            clip_max_duration=45.0,
        )

        self.assertEqual(pipeline.transcriber.model_name, "small")
        self.assertEqual(pipeline.compliance_engine.custom_words, ("acme",))
        self.assertEqual(pipeline.highlight_scorer.min_duration, 20.0)
        self.assertEqual(pipeline.highlight_scorer.max_duration, 45.0)
        self.assertEqual(pipeline.clip_renderer.output_dir, "out")

    def test_device_defaults_to_auto_detect(self):
        pipeline = AutoReelPipeline(output_dir="out")

        self.assertIsNone(pipeline.device)
        self.assertIsNone(pipeline.transcriber.device)

    def test_device_override_is_wired_to_transcriber(self):
        pipeline = AutoReelPipeline(output_dir="out", device="cpu")

        self.assertEqual(pipeline.transcriber.device, "cpu")


if __name__ == "__main__":
    unittest.main()

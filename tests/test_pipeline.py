import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
sys.path.insert(0, _REPO)

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

    def test_uncensored_violations_are_flagged_distinctly_from_censored(self):
        violations = [Violation(word="darn", start=1.0, end=1.5, category="profanity")]

        censored_report = SupervisorReport(
            source_path="stream.mp4", violations=violations, clips=[], clip_paths=[], censored=True
        )
        uncensored_report = SupervisorReport(
            source_path="stream.mp4", violations=violations, clips=[], clip_paths=[], censored=False
        )

        self.assertIn("censored", censored_report.to_markdown().lower())
        self.assertIn("disabled", uncensored_report.to_markdown().lower())
        self.assertNotEqual(censored_report.to_markdown(), uncensored_report.to_markdown())


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

    def test_censor_profanity_defaults_to_true(self):
        pipeline = AutoReelPipeline(output_dir="out")

        self.assertTrue(pipeline.censor_profanity)

    def test_censor_profanity_can_be_disabled(self):
        pipeline = AutoReelPipeline(output_dir="out", censor_profanity=False)

        self.assertFalse(pipeline.censor_profanity)

    def test_face_tracking_defaults_to_true_and_is_wired_to_renderer(self):
        pipeline = AutoReelPipeline(output_dir="out")

        self.assertTrue(pipeline.face_tracking)
        self.assertTrue(pipeline.clip_renderer.face_tracking)

    def test_face_tracking_can_be_disabled(self):
        pipeline = AutoReelPipeline(output_dir="out", face_tracking=False)

        self.assertFalse(pipeline.face_tracking)
        self.assertFalse(pipeline.clip_renderer.face_tracking)


if __name__ == "__main__":
    unittest.main()


# ═════════════════════════════════════════════════════════════════════════════
# A clip is not a stream
#
# A channel of full VODs should not fill up with thirty-second Twitch
# highlights. Clips go to Rumble (which takes shorts) and the social
# accounts; the main YouTube channel gets streams only.
# ═════════════════════════════════════════════════════════════════════════════

def test_a_short_video_is_a_clip():
    import main

    assert main.CLIP_MAX_SECONDS >= 90, \
        "Twitch clips run to about 90 seconds"


def test_the_clip_threshold_never_catches_a_stream():
    """A four-hour VOD must never be mistaken for a highlight."""
    import main

    four_hours = 4 * 60 * 60
    assert four_hours > main.CLIP_MAX_SECONDS * 10


def test_an_unmeasurable_duration_is_not_treated_as_zero(monkeypatch):
    """ffprobe missing or refusing the file must fall through to the
    normal path, not classify every video as a zero-second clip."""
    from utils.ffmpeg_tools import media_duration

    monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(
        FileNotFoundError("ffprobe")))
    assert media_duration("whatever.mp4") is None


def test_duration_is_read_from_ffprobe(monkeypatch):
    from utils.ffmpeg_tools import media_duration

    class Done:
        stdout = b"42.5\n"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: Done())
    assert media_duration("clip.mp4") == 42.5


def test_missing_reel_file_lists_the_clips_that_do_exist(tmp_path, monkeypatch):
    """A basename match is no help when the guess was the wrong NAME
    rather than the wrong folder - which is exactly what happens with
    clips, since they are named after whatever the streamer called them."""
    import main

    watch = tmp_path / "watch_folder"
    uploaded = tmp_path / "uploaded"
    watch.mkdir()
    uploaded.mkdir()
    (watch / "ff.mp4").write_bytes(b"clip")
    (watch / "long stream.mp4").write_bytes(b"stream")
    (watch / "notes.txt").write_text("not a video")

    class Cfg:
        class general:
            watch_folder = str(watch)
            uploaded_folder = str(uploaded)
            supported_formats = (".mp4",)

    monkeypatch.setattr(
        main, "media_duration",
        lambda p: 30.0 if p.endswith("ff.mp4") else 4 * 60 * 60)
    monkeypatch.setattr("utils.ffmpeg_tools.media_duration",
                        lambda p: 30.0 if p.endswith("ff.mp4") else 4 * 60 * 60)

    found = main._find_clips(Cfg)
    assert len(found) == 1
    assert "ff.mp4" in found[0]
    assert "30s" in found[0]


# ═════════════════════════════════════════════════════════════════════════════
# A clip has to be vertical to be a Short
# ═════════════════════════════════════════════════════════════════════════════

def test_rumble_files_clips_as_shorts_only_if_they_are_vertical():
    """Rumble decides Shorts by aspect ratio, not by duration - an 18
    second 16:9 clip lands in Videos next to the five-hour streams."""
    from autoreel.clip_maker import VERTICAL_HEIGHT, VERTICAL_WIDTH

    assert VERTICAL_HEIGHT > VERTICAL_WIDTH
    assert round(VERTICAL_HEIGHT / VERTICAL_WIDTH, 4) == round(16 / 9, 4)


def test_instagram_posts_a_clip_every_25_minutes():
    """A stream is cut into several clips at once, so they need pacing -
    posting six back to back is a burst however good they are. 25 minutes
    is what the account owner asked for."""
    import json

    with open(os.path.join(_UPLOADER, "config.json")) as f:
        instagram = json.load(f)["posting"]["platforms"]["instagram"]
    assert instagram["min_minutes_between"] == 25
    assert instagram["enabled"] is True


def test_the_instagram_cap_matches_instagram_s_own_limit():
    """Set to 50 rather than 0, because 0 means unlimited to the guard -
    and the 51st post would then be attempted, fail as a real API error,
    and count toward the circuit breaker."""
    import json

    with open(os.path.join(_UPLOADER, "config.json")) as f:
        instagram = json.load(f)["posting"]["platforms"]["instagram"]
    assert instagram["daily_cap"] == 50


def test_a_clip_takes_its_title_from_the_filename():
    """A batch of eleven must not stop eleven times to ask for something
    already on disk."""
    import sys
    sys.path.insert(0, _UPLOADER)
    from utils.social_promoter import clip_title

    assert clip_title("Stackswopo twitch Ayo.mp4") == "Ayo"
    assert clip_title("Stackswopo twitch clips ban that....mp4") == "ban that"


def test_clip_length_and_clip_routing_are_separate_settings():
    """They shared one key, so raising the routing threshold to 3 minutes
    silently made every rendered clip 3 minutes long."""
    import json

    with open(os.path.join(_UPLOADER, "config.json")) as f:
        clips = json.load(f)["clips"]
    assert clips["treat_as_clip_under_seconds"] > clips["max_seconds"]
    assert clips["max_seconds"] <= 90, "Reels and Shorts want short clips"


def test_streams_are_cut_into_clips_automatically():
    import json

    with open(os.path.join(_UPLOADER, "config.json")) as f:
        clips = json.load(f)["clips"]
    assert clips["auto_from_streams"] is True
    assert clips["count"] >= 1

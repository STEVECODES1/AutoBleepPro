"""
YouTube and Rumble run together, and the censor pass still runs once.

Both uploads spend nearly all their time on the network and neither
depends on the other, so doing them one after the other spent the sum of
two waits for no reason. The risk in changing that is not the concurrency
itself - it is everything the two threads touch: the transcript cache,
the duplicate store, and the terminal.
"""

import json
import os
import shutil
import sys
import threading
import time

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)


UPLOAD_SECONDS = 0.4


class Recorder:
    """Shared scoreboard: when each upload ran, and how often."""

    def __init__(self):
        self.spans = {}
        self.censor_calls = 0
        self.lock = threading.Lock()

    def span(self, platform, started, ended):
        with self.lock:
            self.spans[platform] = (started, ended)

    def overlapped(self):
        if len(self.spans) < 2:
            return False
        (a_start, a_end), (b_start, b_end) = self.spans.values()
        return a_start < b_end and b_start < a_end


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """A loaded config, a video, and fake uploaders that take time."""
    import main
    from utils.config import load_config

    with open(os.path.join(_UPLOADER, "config.json"), encoding="utf-8") as f:
        raw = json.load(f)
    raw["general"]["watch_folder"] = "./watch_folder"
    raw["general"]["cleanup"] = {"source_video": "keep"}
    raw["clips"]["auto_from_streams"] = False
    raw["features"]["social_promoter"]["enabled"] = False
    raw["posting"]["enabled"] = False
    raw["general"]["enable_desktop_notifications"] = False
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")

    cfg = load_config(str(config_path), str(tmp_path / ".env"))
    # A failing upload here must not spend the real retry delays - those
    # are minutes long by design, which is right in production and is a
    # hung test suite here.
    cfg.general.max_retries = 1
    cfg.general.retry_delays = (0,)
    os.makedirs(cfg.general.watch_folder, exist_ok=True)
    video = os.path.join(cfg.general.watch_folder, "A Stream 2026-08-10.mp4")
    with open(video, "wb") as f:
        f.write(b"pretend video bytes" * 100)

    recorder = Recorder()

    def uploader(platform):
        class Fake:
            def __init__(self, *args, **kwargs):
                pass

            def upload(self, path, *args, **kwargs):
                started = time.monotonic()
                callback = kwargs.get("progress_callback")
                # Fired near-immediately, matching a real upload's first
                # progress tick - the moment bytes actually start moving,
                # not the moment the transfer finishes. Rumble's head
                # start for YouTube is keyed off exactly this.
                if callback:
                    callback(1)
                time.sleep(UPLOAD_SECONDS)
                recorder.span(platform, started, time.monotonic())
                if callback:
                    callback(100)
                return f"https://{platform}.example/watch"

            def get_service(self):
                return None
        return Fake

    monkeypatch.setattr(main, "YouTubeUploader", uploader("youtube"))
    monkeypatch.setattr(main, "RumbleUploader", uploader("rumble"))

    class CensorResult:
        output_path = video
        violation_count = 0
        was_censored = False
        censored_words = []

    def fake_censor(*args, **kwargs):
        recorder.censor_calls += 1
        return CensorResult()

    monkeypatch.setattr(main, "censor_video", fake_censor)
    # ffprobe is not installed in CI; a stream is anything long.
    monkeypatch.setattr(main, "media_duration", lambda path: 9000.0)

    return main, cfg, video, recorder


def run(main, cfg, video, tmp_path, **kwargs):
    from utils.duplicate_checker import DuplicateChecker
    from utils.logging_setup import setup_logger

    checker = DuplicateChecker(str(tmp_path / "uploads.json"))
    logs = str(tmp_path / "logs")
    return main.process_file(
        video, cfg, "A Stream", checker,
        setup_logger("youtube", logs), setup_logger("rumble", logs),
        False, existing_youtube_videos=[], existing_rumble_videos=[],
        allow_prompt=False, **kwargs)


def test_both_platforms_upload_at_the_same_time(scene, tmp_path):
    main, cfg, video, recorder = scene

    results = run(main, cfg, video, tmp_path)

    assert results["youtube"].startswith("https://youtube")
    assert results["rumble"].startswith("https://rumble")
    assert recorder.overlapped(), \
        "the two uploads ran one after the other, not together"


def test_the_censor_pass_still_runs_exactly_once(scene, tmp_path):
    """Two threads asking for the censored copy at once would transcribe
    the same video twice, on one GPU. The paths are resolved first."""
    main, cfg, video, recorder = scene

    run(main, cfg, video, tmp_path)

    assert recorder.censor_calls == 1


def test_one_platform_failing_does_not_stop_the_other(scene, tmp_path,
                                                      monkeypatch):
    main, cfg, video, recorder = scene

    class Broken:
        def __init__(self, *args, **kwargs):
            pass

        def upload(self, *args, **kwargs):
            raise RuntimeError("rumble is having a day")

    monkeypatch.setattr(main, "RumbleUploader", Broken)

    results = run(main, cfg, video, tmp_path)

    assert results["youtube"].startswith("https://youtube")
    assert results["rumble"].startswith("FAILED")


def test_both_outcomes_are_recorded_for_dedup(scene, tmp_path):
    """Two threads writing one JSON file is how an upload record gets
    lost, and a lost record is a re-upload of a whole stream."""
    from utils.duplicate_checker import DuplicateChecker, hash_file

    main, cfg, video, recorder = scene
    store = tmp_path / "uploads.json"

    from utils.logging_setup import setup_logger
    checker = DuplicateChecker(str(store))
    logs = str(tmp_path / "logs")
    main.process_file(video, cfg, "A Stream", checker,
                      setup_logger("youtube", logs), setup_logger("rumble", logs),
                      False, existing_youtube_videos=[],
                      existing_rumble_videos=[], allow_prompt=False)

    reread = DuplicateChecker(str(store))
    assert reread.is_fully_uploaded(hash_file(video),
                                    platforms=("youtube", "rumble"))


def test_a_single_platform_run_does_not_go_parallel(scene, tmp_path):
    """--only youtube has nothing to overlap with, and the single-line
    progress readout is nicer when nothing competes for the terminal."""
    main, cfg, video, recorder = scene

    results = run(main, cfg, video, tmp_path, only_platform="youtube")

    assert results["youtube"].startswith("https://youtube")
    assert "rumble" not in results
    assert not recorder.overlapped()


def test_parallel_can_be_turned_off(scene, tmp_path):
    main, cfg, video, recorder = scene
    cfg.general.speed = dict(cfg.general.speed or {})
    cfg.general.speed["parallel_uploads"] = False

    run(main, cfg, video, tmp_path)

    assert not recorder.overlapped(), "parallel_uploads: false was ignored"


# ═════════════════════════════════════════════════════════════════════════════
# RUMBLE FIRST
#
# "make sure the first thing to do is upload to rumble ... and when its
# uploading you can start the other". Both still run concurrently - that
# part of the design is right, the two uploads really are independent -
# but Rumble is meant to be the one already moving before YouTube's
# transfer starts competing with it for bandwidth. Keyed off Rumble's own
# progress callback rather than a fixed delay: that only fires once the
# browser is genuinely sending bytes, after login/form-fill/category-pick
# are already behind it - not the moment the thread merely starts.
# ═════════════════════════════════════════════════════════════════════════════

def test_rumble_starts_before_youtube(scene, tmp_path):
    main, cfg, video, recorder = scene

    run(main, cfg, video, tmp_path)

    rumble_start = recorder.spans["rumble"][0]
    youtube_start = recorder.spans["youtube"][0]
    assert rumble_start <= youtube_start, \
        "YouTube started no later than Rumble - Rumble was not given the lead"


def test_youtube_waits_for_rumbles_first_progress_tick(scene, tmp_path,
                                                        monkeypatch):
    """A Rumble that is slow to even START sending (a long login, a slow
    form fill) must make YouTube wait for it - not just be submitted
    after it into a thread pool that runs both immediately anyway."""
    main, cfg, video, recorder = scene
    DELAY = 0.3

    class SlowToStart:
        def __init__(self, *args, **kwargs):
            pass

        def upload(self, path, *args, **kwargs):
            started = time.monotonic()
            time.sleep(DELAY)
            callback = kwargs.get("progress_callback")
            if callback:
                callback(1)
            recorder.span("rumble", started, time.monotonic())
            if callback:
                callback(100)
            return "https://rumble.example/watch"

        def get_service(self):
            return None

    monkeypatch.setattr(main, "RumbleUploader", SlowToStart)

    run(main, cfg, video, tmp_path)

    youtube_start = recorder.spans["youtube"][0]
    rumble_start = recorder.spans["rumble"][0]
    assert youtube_start >= rumble_start + DELAY - 0.05, \
        "YouTube started before Rumble's transfer actually began"


def test_a_failed_rumble_releases_youtube_immediately(scene, tmp_path,
                                                       monkeypatch):
    """A Rumble that fails before ever reaching a real transfer (bad
    login, changed page) must not make YouTube sit through the full
    head-start timeout for an upload that was already over."""
    main, cfg, video, recorder = scene

    class DiesImmediately:
        def __init__(self, *args, **kwargs):
            pass

        def upload(self, *args, **kwargs):
            raise RuntimeError("could not even log in")

        def get_service(self):
            return None

    monkeypatch.setattr(main, "RumbleUploader", DiesImmediately)

    started = time.monotonic()
    results = run(main, cfg, video, tmp_path)
    elapsed = time.monotonic() - started

    assert results["youtube"].startswith("https://youtube")
    assert elapsed < main.RUMBLE_HEAD_START_TIMEOUT, \
        "YouTube waited out the full head-start timeout for a dead Rumble"


def test_giving_up_on_a_stuck_rumble_after_the_timeout(scene, tmp_path,
                                                        monkeypatch, capsys):
    """Rumble that never calls its progress callback AND never finishes
    (or fails) within the window - a genuinely stuck browser - must not
    hold YouTube forever either."""
    main, cfg, video, recorder = scene
    monkeypatch.setattr(main, "RUMBLE_HEAD_START_TIMEOUT", 0.1)

    class NeverReports:
        def __init__(self, *args, **kwargs):
            pass

        def upload(self, path, *args, **kwargs):
            started = time.monotonic()
            time.sleep(UPLOAD_SECONDS)
            recorder.span("rumble", started, time.monotonic())
            # No progress_callback call at all - it never even gets
            # that far, but it does eventually return.
            return "https://rumble.example/watch"

        def get_service(self):
            return None

    monkeypatch.setattr(main, "RumbleUploader", NeverReports)

    results = run(main, cfg, video, tmp_path)

    assert results["youtube"].startswith("https://youtube")
    out = capsys.readouterr().out
    assert "starting anyway" in out

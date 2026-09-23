"""
A clip's social-platform offer (Instagram/Facebook/Shorts, through the
clip queue) used to start only after Rumble's own upload had fully
finished - even though the two do not share a resource. Rumble's upload
is browser/network I/O; offer()'s own per-platform censor pass (when a
platform asks for one) is GPU work Rumble never touches. Waiting for one
before starting the other was dead time, not a real dependency - the
same shape of fix as YouTube and Rumble already running together for a
full stream (see test_parallel_uploads.py).

Modelled directly on that file's Recorder/scene pattern.
"""

import json
import os
import sys
import threading
import time

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

WORK_SECONDS = 0.4


class Recorder:
    def __init__(self):
        self.spans = {}
        self.lock = threading.Lock()

    def span(self, name, started, ended):
        with self.lock:
            self.spans[name] = (started, ended)

    def overlapped(self):
        if len(self.spans) < 2:
            return False
        (a_start, a_end), (b_start, b_end) = self.spans.values()
        return a_start < b_end and b_start < a_end


@pytest.fixture
def scene(tmp_path, monkeypatch):
    import main
    from utils.config import load_config

    with open(os.path.join(_UPLOADER, "config.json"), encoding="utf-8") as f:
        raw = json.load(f)
    raw["general"]["watch_folder"] = "./watch_folder"
    raw["general"]["cleanup"] = {"source_video": "keep"}
    raw["clips"]["auto_from_streams"] = False
    raw["features"]["social_promoter"]["enabled"] = False
    raw["general"]["enable_desktop_notifications"] = False
    raw["posting"]["enabled"] = True
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")

    cfg = load_config(str(config_path), str(tmp_path / ".env"))
    cfg.general.max_retries = 1
    cfg.general.retry_delays = (0,)
    os.makedirs(cfg.general.watch_folder, exist_ok=True)
    # A short filename/duration so this routes as a CLIP - Rumble only,
    # not YouTube - which is the whole point: only one upload job is
    # dispatched, so any overlap has to come from the clip offer thread,
    # not from the existing YouTube+Rumble parallelism.
    video = os.path.join(cfg.general.watch_folder, "funny moment.mp4")
    with open(video, "wb") as f:
        f.write(b"pretend clip bytes" * 100)

    recorder = Recorder()

    class FakeRumble:
        def __init__(self, *args, **kwargs):
            pass

        def upload(self, path, *args, **kwargs):
            started = time.monotonic()
            callback = kwargs.get("progress_callback")
            if callback:
                callback(1)
            time.sleep(WORK_SECONDS)
            recorder.span("rumble", started, time.monotonic())
            if callback:
                callback(100)
            return "https://rumble.example/watch"

    monkeypatch.setattr(main, "RumbleUploader", FakeRumble)
    # media_duration is read directly by process_file's clip-routing
    # check - short enough to be treated as a clip.
    monkeypatch.setattr(main, "media_duration", lambda path: 20.0)

    class CensorResult:
        output_path = video
        violation_count = 0
        was_censored = False
        censored_words = []

    # This project's own config.json (loaded as this fixture's base) has
    # instagram.censor_uploads: true, so instagram_clip_path() calls the
    # real censor_video() - mocked here the same way
    # test_parallel_uploads.py does it, so this test is isolated from
    # whether ffmpeg/moviepy happen to be installed in whatever
    # environment runs it.
    monkeypatch.setattr(main, "censor_video", lambda *a, **k: CensorResult())

    import utils.clip_queue as clip_queue_mod

    def fake_offer(posting, config, video_path, fallback_caption="",
                   platforms=None, dry_run=False):
        started = time.monotonic()
        time.sleep(WORK_SECONDS)
        recorder.span("offer", started, time.monotonic())
        return {"instagram": "posted"}

    monkeypatch.setattr(clip_queue_mod, "offer", fake_offer)

    return main, cfg, video, recorder


def run(main, cfg, video, tmp_path, **kwargs):
    from utils.duplicate_checker import DuplicateChecker
    from utils.logging_setup import setup_logger

    checker = DuplicateChecker(str(tmp_path / "uploads.json"))
    logs = str(tmp_path / "logs")
    return main.process_file(
        video, cfg, None, checker,
        setup_logger("youtube", logs), setup_logger("rumble", logs),
        False, existing_youtube_videos=[], existing_rumble_videos=[],
        allow_prompt=False, **kwargs)


def test_the_clip_offer_overlaps_the_rumble_upload(scene, tmp_path):
    main, cfg, video, recorder = scene

    results = run(main, cfg, video, tmp_path)

    assert results.get("rumble", "").startswith("https://rumble")
    assert recorder.overlapped(), \
        "the clip's Rumble upload and its social-platform offer ran one " \
        "after the other, not together"


def test_the_offer_thread_is_started_before_rumbles_own_dispatch():
    """Read out of the source: starting it any later than this defeats
    the point - it would just be a differently-shaped serial wait."""
    path = os.path.join(_UPLOADER, "main.py")
    body = open(path, encoding="utf-8").read()

    offer_thread_at = body.index("_clip_offer_thread = threading.Thread")
    dispatch_at = body.index("# --- Dispatch ---")
    assert offer_thread_at < dispatch_at, \
        "the clip offer thread must start before Rumble's own dispatch"


def test_clip_reels_is_always_read_after_the_thread_is_joined():
    path = os.path.join(_UPLOADER, "main.py")
    body = open(path, encoding="utf-8").read()

    join_at = body.index("_clip_offer_thread.join()")
    read_at = body.index('_clip_offer_result.get("value", {})')
    assert join_at < read_at, \
        "clip_reels must not be read before the offer thread is joined"


def test_a_non_clip_upload_starts_no_offer_thread(scene, tmp_path,
                                                   monkeypatch):
    """A full stream is not routed through Rumble-only clip dispatch and
    must not spin up a thread that has nothing to offer."""
    import main

    main, cfg, video, recorder = scene
    monkeypatch.setattr(main, "media_duration", lambda path: 9000.0)

    class FakeYouTube:
        def __init__(self, *a, **k):
            pass

        def upload(self, *a, **k):
            return "https://youtube.example/watch"

    monkeypatch.setattr(main, "YouTubeUploader", FakeYouTube)
    monkeypatch.setattr(main, "censor_video",
                        lambda *a, **k: type(
                            "R", (), {"output_path": video,
                                     "violation_count": 0,
                                     "was_censored": False,
                                     "censored_words": []})())

    run(main, cfg, video, tmp_path)

    assert "offer" not in recorder.spans


def test_offer_still_runs_when_rumble_fails(scene, tmp_path, monkeypatch):
    """The two are independent - a Rumble failure must not take the
    clip's other platforms down with it."""
    main, cfg, video, recorder = scene

    class Broken:
        def __init__(self, *a, **k):
            pass

        def upload(self, *a, **k):
            raise RuntimeError("rumble is having a day")

    monkeypatch.setattr(main, "RumbleUploader", Broken)

    run(main, cfg, video, tmp_path)

    assert "offer" in recorder.spans

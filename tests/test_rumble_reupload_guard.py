"""Rumble's dedup fallback has never once fired. This is the fix.

From publishers.log:

    [Rumble] Uploading... 100%
    [Rumble] WARNING: Rumble never showed a video link.
    [Rumble] No link came back. Checking https://rumble.com/user/BinScripts
             for the video...
    [Rumble] NOT on the channel. ... recorded as a failure and the next
             run will retry it.

That check happens the moment the upload page gives up - no time for
Rumble's own listing page to catch up with what was just published. If
the video genuinely landed a little later, the run above records a
failure, and the NEXT run had nothing that would have caught it: the
pre-upload dedup path fell back to `existing_rumble_videos`, which comes
from `fetch_rumble_videos(rss_url)` - and Rumble does not publish an RSS
feed at all, at any address, ever. That branch has been permanently
empty since the day it was written. So a retry after RUMBLE_UNCONFIRMED
went straight back into uploading the same multi-gigabyte file a second
time, with nothing to catch it if the first one actually worked.

find_on_channel already existed for the post-upload check. It is wired
into the PRE-upload path now too, so a retry checks the live channel
before spending bandwidth on it again.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)


# ── _rumble_channel_url: one source of truth for both checks ─────────────

def test_channel_url_is_used_first():
    import main

    class Cfg:
        class rumble:
            channel_url = "https://rumble.com/user/BinScripts"
            rss_url = "https://rumble.com/user/SomeoneElse/index.xml"

    assert main._rumble_channel_url(Cfg) == "https://rumble.com/user/BinScripts"


def test_falls_back_to_the_rss_address_with_the_filename_stripped():
    import main

    class Cfg:
        class rumble:
            channel_url = ""
            rss_url = "https://rumble.com/user/BinScripts/index.xml"

    assert main._rumble_channel_url(Cfg) == "https://rumble.com/user/BinScripts"


def test_both_the_pre_and_post_upload_checks_agree_on_the_channel():
    """A mismatch here would mean the two checks look at different
    channels and can disagree with each other for no reason."""
    import main

    source = open(os.path.join(_UPLOADER, "main.py"), encoding="utf-8").read()

    assert source.count("_rumble_channel_url(cfg)") >= 2


# ── the pre-upload skip, end to end through process_file ─────────────────

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
    raw["posting"]["enabled"] = False
    raw["general"]["enable_desktop_notifications"] = False
    raw["rumble"]["skip_if_exists"] = True
    raw["rumble"]["channel_url"] = "https://rumble.com/user/BinScripts"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")

    cfg = load_config(str(config_path), str(tmp_path / ".env"))
    cfg.general.max_retries = 1
    cfg.general.retry_delays = (0,)
    os.makedirs(cfg.general.watch_folder, exist_ok=True)
    video = os.path.join(
        cfg.general.watch_folder,
        '"REACTING TO BRANDRISK BOXING" 8-23-26 Stackswopo Stream.mp4')
    with open(video, "wb") as f:
        f.write(b"pretend video bytes" * 100)

    rumble_calls = []

    class FakeYouTube:
        def __init__(self, *a, **k):
            pass

        def upload(self, path, *a, **k):
            callback = k.get("progress_callback")
            if callback:
                callback(100)
            return "https://youtube.example/watch"

        def get_service(self):
            return None

    class FakeRumble:
        def __init__(self, *a, **k):
            pass

        def upload(self, path, *a, **k):
            rumble_calls.append(path)
            callback = k.get("progress_callback")
            if callback:
                callback(100)
            # Slug matches the title directly, so the pre-existing POST-upload
            # verification (_confirm_on_rumble) accepts it on the spot and
            # never touches find_on_channel - keeping these tests isolated
            # to the NEW pre-upload check.
            return ("https://rumble.com/user/BinScripts/"
                    "v7fresh-reacting-to-brandrisk-boxing-stackswopo.html")

    monkeypatch.setattr(main, "YouTubeUploader", FakeYouTube)
    monkeypatch.setattr(main, "RumbleUploader", FakeRumble)

    class CensorResult:
        output_path = video
        violation_count = 0
        was_censored = False
        censored_words = []

    monkeypatch.setattr(main, "censor_video", lambda *a, **k: CensorResult())
    monkeypatch.setattr(main, "media_duration", lambda path: 9000.0)

    return main, cfg, video, rumble_calls


def _run(main, cfg, video, tmp_path):
    from utils.duplicate_checker import DuplicateChecker
    from utils.logging_setup import setup_logger

    checker = DuplicateChecker(str(tmp_path / "uploads.json"))
    logs = str(tmp_path / "logs")
    return main.process_file(
        video, cfg, "REACTING TO BRANDRISK BOXING", checker,
        setup_logger("youtube", logs), setup_logger("rumble", logs),
        False, existing_youtube_videos=[], existing_rumble_videos=[],
        allow_prompt=False)


def test_a_video_already_on_the_channel_is_not_reuploaded(scene, tmp_path, monkeypatch):
    """The scenario in the log, one run later: the previous attempt was
    marked RUMBLE_UNCONFIRMED, but the video actually landed and the
    listing page has caught up by the time this run starts."""
    main, cfg, video, rumble_calls = scene
    from utils import channel_vods

    already_there = "https://rumble.com/user/BinScripts/v7abc-reacting-to-brandrisk-boxing.html"
    monkeypatch.setattr(channel_vods, "find_on_channel",
                        lambda channel, title, **k: already_there)

    result = _run(main, cfg, video, tmp_path)

    assert rumble_calls == [], (
        "the video was already on the channel and got uploaded again")
    assert result["rumble"] == already_there


def test_a_video_genuinely_not_there_still_uploads_normally(scene, tmp_path, monkeypatch):
    """The common case - nothing on the channel yet - must still upload,
    not get stuck refusing forever."""
    main, cfg, video, rumble_calls = scene
    from utils import channel_vods

    monkeypatch.setattr(channel_vods, "find_on_channel",
                        lambda channel, title, **k: "")

    result = _run(main, cfg, video, tmp_path)

    assert len(rumble_calls) == 1
    assert result["rumble"].startswith("https://rumble.com")


def test_the_channel_check_failing_does_not_block_the_upload(scene, tmp_path, monkeypatch):
    """A network hiccup on this extra check must fall through to the
    existing upload-then-verify path, not add a new way to fail."""
    main, cfg, video, rumble_calls = scene
    from utils import channel_vods

    def explode(channel, title, **k):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(channel_vods, "find_on_channel", explode)

    result = _run(main, cfg, video, tmp_path)

    assert len(rumble_calls) == 1, "a failed check should not skip uploading"
    assert result["rumble"].startswith("https://rumble.com")


def test_skip_if_exists_off_never_calls_the_channel_check(scene, tmp_path, monkeypatch):
    main, cfg, video, rumble_calls = scene
    cfg.rumble.skip_if_exists = False
    from utils import channel_vods

    calls = []
    monkeypatch.setattr(
        channel_vods, "find_on_channel",
        lambda channel, title, **k: calls.append(1) or "")

    _run(main, cfg, video, tmp_path)

    assert not calls, "the channel should not be checked when the feature is off"
    assert len(rumble_calls) == 1


def test_a_generic_title_does_not_trigger_the_channel_check(scene, tmp_path, monkeypatch):
    """The same reason the RSS-title match already skips a generic title:
    every stream that lost its real title would match every other one on
    the channel."""
    main, cfg, video, rumble_calls = scene
    cfg.general.default_title = "Gaming Stream"
    from utils import channel_vods

    calls = []
    monkeypatch.setattr(
        channel_vods, "find_on_channel",
        lambda channel, title, **k: calls.append(1) or "")

    generic_video = os.path.join(cfg.general.watch_folder, "Gaming Stream.mp4")
    with open(generic_video, "wb") as f:
        f.write(b"x" * 100)
    from utils.duplicate_checker import DuplicateChecker
    from utils.logging_setup import setup_logger

    checker = DuplicateChecker(str(tmp_path / "uploads.json"))
    logs = str(tmp_path / "logs")
    main.process_file(
        generic_video, cfg, "Gaming Stream", checker,
        setup_logger("youtube", logs), setup_logger("rumble", logs),
        False, existing_youtube_videos=[], existing_rumble_videos=[],
        allow_prompt=False)

    assert not calls


# ── the censor summary: capped, not a raw dump of every instance ─────────
#
# A boxing reaction stream produced a console line that was hundreds of
# raw slurs in one unbroken run - every instance, uncapped, no grouping -
# which buried every progress line after it and is exactly the kind of
# text that gets screenshotted and pasted somewhere it should not be.
# censor_video's own _report_risk already prints a categorized, capped
# breakdown separately; this line only needed the same discipline.

def test_the_censor_line_is_capped_and_grouped(scene, tmp_path, monkeypatch, capsys):
    main, cfg, video, _rumble_calls = scene

    class CensorResult:
        output_path = video
        violation_count = 157
        was_censored = True
        censored_words = (["nigga"] * 70 + ["fuck"] * 40 + ["faggot"] * 15
                          + ["ass"] * 10 + ["shit"] * 7 + ["pussy"] * 6
                          + ["retard"] * 4 + ["bitch"] * 3 + ["damn"] * 2)

    monkeypatch.setattr(main, "censor_video", lambda *a, **k: CensorResult())
    from utils import channel_vods
    monkeypatch.setattr(channel_vods, "find_on_channel",
                        lambda channel, title, **k: "")

    _run(main, cfg, video, tmp_path)
    printed = capsys.readouterr().out

    line = next(ln for ln in printed.splitlines() if ln.startswith("[Censor] Silenced"))
    assert "157 word(s)" in line
    assert "9 distinct" in line
    assert line.count(",") <= 8, f"line is not capped: {line!r}"
    assert "nigga x70" in line


def test_a_light_stream_is_not_padded_with_more_ceremony(scene, tmp_path, monkeypatch, capsys):
    main, cfg, video, _rumble_calls = scene

    class CensorResult:
        output_path = video
        violation_count = 1
        was_censored = True
        censored_words = ["shit"]

    monkeypatch.setattr(main, "censor_video", lambda *a, **k: CensorResult())
    from utils import channel_vods
    monkeypatch.setattr(channel_vods, "find_on_channel",
                        lambda channel, title, **k: "")

    _run(main, cfg, video, tmp_path)
    printed = capsys.readouterr().out

    line = next(ln for ln in printed.splitlines() if ln.startswith("[Censor] Silenced"))
    assert "1 word(s) (1 distinct): shit" in line

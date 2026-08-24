"""Every captionless clip from one stream got the identical Rumble title.

From a real run:

    [Clip] Title from the filename: Stackswopo - Just Doing My Job - Full
           Stream                                              (Clip 06)
    [Rumble] Uploading... 100% ... Published:
             https://rumble.com/v7ekvti-...-full-stream.html
    ...
    [Clip] Title from the filename: Stackswopo - Just Doing My Job - Full
           Stream                                              (Clip 07)
    [Rumble] Video already exists on Rumble -> https://rumble.com/v7ekvti
             -stackswopo-just-doing-my-job-full-stream.html
    [YouTube] Skipped - --only rumble.

Clip 07 is a different 0.3-minute clip from Clip 06. It never reached
Rumble - it read as a duplicate of Clip 06 (or of the parent stream) and
was skipped, silently, because the two clips ended up with the exact
same title.

clip_title() strips a trailing "- Clip NN" from the filename - correct
for a caption someone will read, e.g. "Doing amazing... - Clip 03"
becomes "Doing amazing...". It is used for a second, different purpose
in main.py's fallback-when-no-caption-sidecar path: this same stripped
string becomes the clip's own UPLOAD TITLE, and the Rumble dedup key.

When a clip's filename is just "<stream title> - Clip NN" with nothing
else distinguishing it - the shape a stream's own auto-cut clips get
whenever one carries no spoken-line caption - stripping the number
collapses EVERY such clip from that stream onto the identical title. The
first uploads. Every clip after it reads as "already exists" and is
never uploaded at all - not "duplicated," gone: the video simply never
reaches Rumble, with no error and no sign anything is wrong beyond a log
line that reads exactly like ordinary, correct dedup behaviour.
"""

from __future__ import annotations

import json
import os
import re
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)


# ── the collision, and the fix, at the source ────────────────────────────

def test_clip_title_itself_still_strips_the_number_for_captions():
    """This half is correct and must not change - a caption reading
    "Doing amazing world of gumball animations - Clip 03" is worse than
    "Doing amazing world of gumball animations"."""
    from utils.social_promoter import clip_title

    assert clip_title("Yoo Howl - Clip 03.mp4") == "Yoo Howl"


def test_two_captionless_clips_from_one_stream_collide_at_the_source():
    """Confirms the bug exists in the function main.py's fallback calls,
    so the fix has to live at the call site, not inside clip_title()
    itself (which serves captions correctly and must keep doing so)."""
    from utils.social_promoter import clip_title

    a = clip_title("Stackswopo - Just Doing My Job - Full Stream - Clip 06.mp4")
    b = clip_title("Stackswopo - Just Doing My Job - Full Stream - Clip 07.mp4")

    assert a == b, "if this ever stops colliding, the guard below is dead code"


CLIP_NUMBER = re.compile(r"[-\s]+clip\s*(\d+)\s*$", re.I)


def _fallback_title(filename: str) -> str:
    """The exact logic process_file now applies after clip_title()."""
    from utils.social_promoter import clip_title as _clip_title

    stream_title = _clip_title(filename)
    match = CLIP_NUMBER.search(os.path.splitext(filename)[0])
    if match:
        stream_title = f"{stream_title} (Clip {int(match.group(1))})"
    return stream_title


def test_the_fix_makes_every_clip_from_one_stream_distinct():
    titles = {_fallback_title(f"Stackswopo - Just Doing My Job - Full "
                              f"Stream - Clip {n:02d}.mp4") for n in (6, 7, 8)}

    assert len(titles) == 3


def test_the_fix_does_not_touch_a_title_that_was_already_distinct():
    """A clip whose stem carries real content beyond the stream name
    still reads naturally - just with its number appended, which is a
    minor cosmetic cost the guarantee of uniqueness is worth."""
    assert _fallback_title("Yoo Howl - Clip 03.mp4") == "Yoo Howl (Clip 3)"


def test_a_filename_with_no_clip_number_is_left_alone():
    """Not every video reaching this fallback is a numbered clip - the
    number-appending must not fire on something it cannot parse."""
    assert _fallback_title("Some Random Upload.mp4") == "Some Random Upload"


# ── end to end, through process_file, exactly as the log showed it ───────

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

    uploaded_titles = []

    class FakeYouTube:
        def __init__(self, *a, **k):
            pass

        def upload(self, path, *a, **k):
            cb = k.get("progress_callback")
            if cb:
                cb(100)
            return "https://youtube.example/watch"

        def get_service(self):
            return None

    class FakeRumble:
        def __init__(self, *a, **k):
            pass

        def upload(self, path, title, *a, **k):
            uploaded_titles.append(title)
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            cb = k.get("progress_callback")
            if cb:
                cb(100)
            return f"https://rumble.com/user/BinScripts/v7{slug}.html"

    monkeypatch.setattr(main, "YouTubeUploader", FakeYouTube)
    monkeypatch.setattr(main, "RumbleUploader", FakeRumble)

    class CensorResult:
        was_censored = False
        censored_words = []

        def __init__(self, path):
            self.output_path = path
            self.violation_count = 0

    monkeypatch.setattr(main, "censor_video", lambda path, *a, **k: CensorResult(path))
    monkeypatch.setattr(main, "media_duration", lambda path: 18.0)

    from utils import channel_vods
    monkeypatch.setattr(channel_vods, "find_on_channel",
                        lambda channel, title, **k: "")

    return main, cfg, uploaded_titles


def _clip(cfg, name: str, minutes: float = 0.3) -> str:
    """A short clip with no caption sidecar - exactly the shape that
    triggers the filename fallback.

    Distinct content per call, deliberately - two different clips have
    different bytes, and content-hash dedup is a real, separate mechanism
    this test must not accidentally trigger. The bug under test is the
    TITLE collision, which is what happens even when the videos
    themselves are genuinely different."""
    path = os.path.join(cfg.general.watch_folder, name)
    with open(path, "wb") as f:
        f.write(os.urandom(1000))
    return path


def _run(main, cfg, video, tmp_path):
    from utils.duplicate_checker import DuplicateChecker
    from utils.logging_setup import setup_logger

    checker = DuplicateChecker(str(tmp_path / "uploads.json"))
    logs = str(tmp_path / "logs")
    return main.process_file(
        video, cfg, "", checker,
        setup_logger("youtube", logs), setup_logger("rumble", logs),
        False, existing_youtube_videos=[], existing_rumble_videos=[],
        allow_prompt=False)


def test_a_second_captionless_clip_now_reaches_rumble(scene, tmp_path):
    """The exact scenario from the log: two short clips cut from the same
    stream, neither carrying a spoken-line caption."""
    main, cfg, uploaded_titles = scene

    clip_a = _clip(cfg, "Stackswopo - Just Doing My Job - Full Stream - "
                        "Clip 06.mp4")
    clip_b = _clip(cfg, "Stackswopo - Just Doing My Job - Full Stream - "
                        "Clip 07.mp4")

    result_a = _run(main, cfg, clip_a, tmp_path)
    result_b = _run(main, cfg, clip_b, tmp_path)

    assert len(uploaded_titles) == 2, (
        f"clip 07 should have reached Rumble as its own upload; only "
        f"{len(uploaded_titles)} call(s) were made: {uploaded_titles}")
    assert uploaded_titles[0] != uploaded_titles[1]
    assert result_a["rumble"].startswith("https://rumble.com")
    assert result_b["rumble"].startswith("https://rumble.com"), (
        f"clip 07 was not uploaded to Rumble at all: {result_b['rumble']}")
    assert result_a["rumble"] != result_b["rumble"]


def test_a_clip_with_its_own_caption_is_unaffected(scene, tmp_path):
    """The fix only touches the NO-caption fallback path; a clip whose
    sidecar carries a real spoken line keeps using it untouched."""
    main, cfg, uploaded_titles = scene

    clip = _clip(cfg, "Stackswopo - Just Doing My Job - Full Stream - "
                      "Clip 09.mp4")
    with open(os.path.splitext(clip)[0] + ".txt", "w", encoding="utf-8") as f:
        f.write("Imma switch yo ahh\n")

    _run(main, cfg, clip, tmp_path)

    assert uploaded_titles == ["Imma switch yo ahh"]


# ── the log must not blame a flag that was never passed ──────────────────
#
# "[YouTube] Skipped - --only rumble." fired identically whether the user
# actually passed --only rumble on the command line, or whether a clip
# under clips.treat_as_clip_under_seconds got routed to Rumble only by
# internal design. The clip case already explains itself two lines above
# ("treating it as a clip: Rumble + social announcement, NOT the YouTube
# channel") - repeating a WRONG, unrelated reason under a real-looking
# flag name is how a deliberate design decision reads as a malfunction.

def test_only_rumble_flag_still_explains_itself(scene, tmp_path, capsys):
    """The real CLI flag case must keep working exactly as before."""
    main, cfg, _titles = scene
    video = os.path.join(cfg.general.watch_folder, "A Full Stream.mp4")
    with open(video, "wb") as f:
        f.write(os.urandom(1000))

    from utils.duplicate_checker import DuplicateChecker
    from utils.logging_setup import setup_logger

    checker = DuplicateChecker(str(tmp_path / "uploads.json"))
    logs = str(tmp_path / "logs")
    main.process_file(
        video, cfg, "A Full Stream", checker,
        setup_logger("youtube", logs), setup_logger("rumble", logs),
        False, existing_youtube_videos=[], existing_rumble_videos=[],
        allow_prompt=False, only_platform="rumble")

    printed = capsys.readouterr().out
    assert "[YouTube] Skipped - --only rumble." in printed


def test_a_routed_clip_does_not_blame_a_flag_that_was_never_passed(
        scene, tmp_path, capsys):
    main, cfg, _titles = scene
    clip = _clip(cfg, "Stackswopo - Just Doing My Job - Full Stream - "
                      "Clip 10.mp4")

    _run(main, cfg, clip, tmp_path)

    printed = capsys.readouterr().out
    assert "--only rumble" not in printed, (
        "no --only flag was passed for this clip - the clip-routing "
        "message above already explains why YouTube was skipped")
    assert "treating it as a clip" in printed

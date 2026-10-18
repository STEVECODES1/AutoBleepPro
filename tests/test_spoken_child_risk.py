"""What a clip SAYS can be a child-safety problem even when its title is
fine: "...and she was 17" was in a clip's audio with nothing in the
title (10-18 scan). YouTube and TikTok hear the audio; Rumble keeps it."""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from publishers.errors import HeldBack  # noqa: E402
from utils import clip_queue as cq  # noqa: E402


def _clip(tmp_path, said, name="Stream - Clip 01"):
    video = tmp_path / f"{name}.mp4"
    video.write_bytes(b"video")
    (tmp_path / f"{name}_transcript_words.json").write_text(
        json.dumps({"segments": [{"start": 0.0, "end": 6.0, "text": said,
                                  "words": []}]}), encoding="utf-8")
    return str(video)


def test_a_spoken_age_under_18_holds_it_everywhere_but_rumble(tmp_path):
    video = _clip(tmp_path, "nah because she said she was 17 bro")
    for platform in ("youtube_shorts", "instagram", "buffer_tiktok", "facebook"):
        with pytest.raises(HeldBack, match="about children"):
            cq._hold_if_child_risk(platform, video, {})
    cq._hold_if_child_risk("rumble", video, {})


def test_calling_someone_a_pedo_holds_only_the_short(tmp_path):
    video = _clip(tmp_path, "you a pedophile bro get off the app")
    with pytest.raises(HeldBack):
        cq._hold_if_child_risk("youtube_shorts", video, {})
    cq._hold_if_child_risk("instagram", video, {})
    cq._hold_if_child_risk("buffer_x", video, {})


def test_ordinary_numbers_hold_nothing(tmp_path):
    video = _clip(tmp_path, "I'm 12 deep in the city and he's 15 minutes late")
    cq._hold_if_child_risk("youtube_shorts", video, {})


def test_no_transcript_is_no_judgement(tmp_path):
    video = tmp_path / "Other - Clip 02.mp4"
    video.write_bytes(b"video")
    cq._hold_if_child_risk("youtube_shorts", str(video), {})

"""A severe slur - muted or not - keeps a clip off the social platforms,
checked right before each post (the queue's later post included)."""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
sys.path.insert(0, ROOT)

from publishers.errors import HeldBack, PermanentlyRejected  # noqa: E402
from utils import clip_queue  # noqa: E402


def _clip(tmp_path, words):
    clip = tmp_path / "S 18 48 Live 02 - Clip 01.mp4"
    clip.write_bytes(b"x")
    segs = [{"start": 0, "end": 10, "text": " ".join(words),
             "words": [{"word": w, "start": i, "end": i + 0.4} for i, w in enumerate(words)]}]
    (tmp_path / "S 18 48 Live 02 - Clip 01_transcript_words.json").write_text(
        json.dumps({"segments": segs}), encoding="utf-8")
    return str(clip), {"note_folders": [str(tmp_path)], "clips": {}}


def test_one_hard_slur_is_enough(tmp_path):
    path, config = _clip(tmp_path, ["yo", "you", "nigger,", "what"])
    with pytest.raises(HeldBack) as caught:
        clip_queue._hold_if_slurs("youtube_shorts", path, config)
    assert "n*****" in str(caught.value)
    assert isinstance(caught.value, PermanentlyRejected)     # dropped, never retried


def test_the_everyday_word_alone_does_not_hold_a_clip(tmp_path):
    path, config = _clip(tmp_path, ["yo", "my", "nigga", "what's", "good"])
    clip_queue._hold_if_slurs("instagram", path, config)          # no raise


def test_a_clip_full_of_it_is_still_held(tmp_path):
    path, config = _clip(tmp_path, ["nigga", "nigga", "nigga", "bro"])
    with pytest.raises(HeldBack):
        clip_queue._hold_if_slurs("facebook", path, config)


def test_the_hold_says_why_in_the_journal():
    assert "slur" in HeldBack("held: x").journal_note

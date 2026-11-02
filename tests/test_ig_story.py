"""The new-full-stream Instagram Story (utils/ig_story.py)."""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from utils import ig_story  # noqa: E402
from publish_guard import DEFAULT_PLATFORMS  # noqa: E402
from publishers import instagram  # noqa: E402


def test_title_wraps_to_three_lines_at_most():
    text = ig_story.wrap_title("word " * 60)
    assert len(text.split("\n")) <= 3
    assert text.endswith("…")


def test_story_has_its_own_guard_slot_not_a_reel_slot():
    assert DEFAULT_PLATFORMS["instagram_story"]["enabled"] is True
    assert DEFAULT_PLATFORMS["instagram_story"]["daily_cap"] <= 3


def test_no_thumbnail_means_no_story(capsys):
    class Cfg:
        posting = {"enabled": True}
    ig_story.post_later(Cfg(), "", "Title")
    ig_story.post_later(Cfg(), "C:/nope/missing.jpg", "Title")
    assert "skipped" in capsys.readouterr().out


def test_story_container_sends_no_caption(monkeypatch):
    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "1"}

    def fake_post(url, data=None, timeout=None):
        sent.update(data)
        return Resp()

    monkeypatch.setattr(instagram.requests, "post", fake_post)
    pub = instagram.InstagramPublisher({})
    assert pub._create_resumable_container("", False, media_type="STORIES") == "1"
    assert sent["media_type"] == "STORIES"
    assert "caption" not in sent and "share_to_feed" not in sent


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_survives_quotes_and_colons(tmp_path):
    img = tmp_path / "t.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=1280x720", "-frames:v", "1", str(img)],
                   check=True)
    out = tmp_path / "s.mp4"
    assert ig_story.render_story(str(img), "'haPPY' 10:31 \"HALLOWEEN\" %d \\ ok",
                                 str(out))
    assert out.stat().st_size > 0

"""A post on wopovod's Posts tab for every new full stream (10-18, asked
for by the owner). The browser part is checked by hand with --dry-run;
these pin the parts that decide WHAT goes out and WHETHER."""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils import community_post as cp  # noqa: E402


def test_the_video_id_comes_out_of_any_link():
    assert cp.video_id("https://www.youtube.com/watch?v=CVh4QPm8Axs") == "CVh4QPm8Axs"
    assert cp.video_id("https://youtu.be/CVh4QPm8Axs?t=3") == "CVh4QPm8Axs"
    assert cp.video_id("CVh4QPm8Axs") == "CVh4QPm8Axs"
    assert cp.video_id("https://youtube.example/watch") == ""
    assert cp.video_id("FAILED: quota") == ""


def test_the_line_is_the_owners_style_and_names_the_stream():
    text = cp.post_text("STACKSWOPO EXPOSED FOR BEING A FAKE GANGSTER", "CVh4QPm8Axs")
    assert "COME CLICK" in text
    assert "STACKSWOPO EXPOSED FOR BEING A FAKE GANGSTER" in text
    assert text == cp.post_text("STACKSWOPO EXPOSED FOR BEING A FAKE GANGSTER", "CVh4QPm8Axs")


def test_a_video_is_posted_once(tmp_path):
    ledger = str(tmp_path / "community_posts.json")
    assert not cp.already_posted("CVh4QPm8Axs", ledger)
    cp._note("CVh4QPm8Axs", {"at": 1.0, "post": "x", "text": "t"}, ledger)
    assert cp.already_posted("CVh4QPm8Axs", ledger)
    assert cp.post("https://www.youtube.com/watch?v=CVh4QPm8Axs", "t",
                   ledger=ledger, say=lambda *a: None) == "already"


def test_the_suite_never_starts_a_real_post(monkeypatch):
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    assert cp.post_later(object(), "https://www.youtube.com/watch?v=abcdefghijk",
                         "a test stream", say=lambda *a: None) is False
    assert started == []

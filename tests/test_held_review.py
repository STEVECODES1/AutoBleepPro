"""Held clips go to Discord once, and a person's OK lifts only the slur hold."""
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils import held_review  # noqa: E402


def _job(id_, path, err, state="failed", platform="buffer_tiktok", url=""):
    return SimpleNamespace(id=id_, clip_path=str(path), last_error=err, state=state,
                           platform=platform, caption="A line", result_url=url)


def test_only_slur_holds_with_a_file_are_up_for_review(tmp_path):
    clip = tmp_path / "c1.mp4"
    clip.write_bytes(b"x")
    jobs = [_job("a", clip, "held: a severe slur (f*****)"),
            _job("b", clip, "held: about children (YouTube child-safety risk)"),
            _job("c", tmp_path / "gone.mp4", "held: 3 slurs in 26s (limit 3)"),
            _job("d", clip, "", state="done", platform="rumble", url="https://rumble.com/v1")]
    got = held_review.held_clips(SimpleNamespace(list_jobs=lambda: jobs))
    assert list(got) == ["c1.mp4"]
    assert got["c1.mp4"]["jobs"] == ["a"] and got["c1.mp4"]["rumble"] == "https://rumble.com/v1"


def test_an_approved_clip_skips_the_slur_hold_only(tmp_path, monkeypatch):
    monkeypatch.setattr(held_review, "APPROVED", str(tmp_path / "ok.json"))
    held_review._write(held_review.APPROVED, ["c1.mp4"])
    assert held_review.approved(r"D:\x\c1.mp4")
    assert not held_review.approved(r"D:\x\c2.mp4")


def test_nothing_is_sent_without_a_webhook(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert held_review.send_new(say=lambda *_: None) == 0
    held_review.send_at_hold(__file__, "x")          # returns quietly

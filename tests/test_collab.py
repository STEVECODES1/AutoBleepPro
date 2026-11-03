"""Collab with STACKSWOPO GAMES runs on its own (utils/collab.py)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from utils import collab  # noqa: E402


def test_ledger_marks_a_video_done_only_once_accepted(tmp_path):
    ledger = str(tmp_path / "c.json")
    assert not collab.done("abc", ledger)
    collab._note("abc", {"invited": 1}, ledger)
    assert not collab.done("abc", ledger)
    collab._note("abc", {"accepted": 2}, ledger)
    assert collab.done("abc", ledger)
    assert collab._load(ledger)["abc"] == {"invited": 1, "accepted": 2}


def test_a_done_video_never_opens_the_browser(tmp_path, monkeypatch):
    ledger = str(tmp_path / "c.json")
    collab._note("KaDdlXDqAvk", {"accepted": 1}, ledger)
    import utils.chrome_cdp as cdp
    monkeypatch.setattr(cdp, "ensure_chrome",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    assert collab.run("https://www.youtube.com/watch?v=KaDdlXDqAvk", ledger=ledger)


def test_post_later_never_runs_from_tests():
    assert collab.post_later("https://www.youtube.com/watch?v=x") is None

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils import binscripts_weekly as B  # noqa: E402


def test_card_text_is_safe_for_drawtext():
    assert ":" not in B._esc("Johnny Cox: 'Call a cop now!' 😂")
    assert "'" not in B._esc("it's")


def test_headline_wraps_to_two_lines():
    lines = B._lines("Johnny Cox drops wild celebrity comparisons on the street, causing chaos everywhere")
    assert 1 <= len(lines) <= 2 and all(len(l) <= 40 for l in lines)


def test_not_due_without_token(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TOKEN", str(tmp_path / "missing.json"))
    assert not B.due()

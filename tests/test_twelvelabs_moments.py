"""
Twelve Labs as one more clip signal. Nothing here reaches the API.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel import twelvelabs_moments as tl  # noqa: E402


def test_a_three_hour_stream_goes_in_hour_long_pieces():
    plan = tl.pieces(3 * 3600)
    assert all(length <= 60 * 60 for _s, length in plan)
    assert sum(length for _s, length in plan) == 3 * 3600
    assert plan[1][0] == plan[0][1]


def test_the_answer_is_moved_to_stream_time():
    raw = json.dumps({"moments": [
        {"start": 10, "end": 40, "score": 0.9, "why": "chase"},
        {"start": 5, "end": 6, "score": 1.0},          # too short
        {"start": "x", "end": 9},                       # junk
        {"start": 3000, "end": 9999, "score": 2}]})     # past the piece
    found = tl.read_moments(raw, offset=3300, length=3300)
    assert found[0] == (3310.0, 3340.0, 0.9, "chase")
    assert found[1] == (6300.0, 6600.0, 1.0, "")
    assert len(found) == 2
    assert tl.read_moments("not json", 0, 60) == []


def test_it_lifts_a_window_but_does_not_decide():
    curve = tl.seen_curve([(100, 130, 0.8, "")], 600)
    assert tl.seen_bonus(curve, 110, 140) == pytest.approx(1.4)
    assert tl.seen_bonus(curve, 300, 330) == 1.0
    assert tl.seen_bonus([], 0, 10) == 1.0
    assert tl.seen_bonus(curve, 0, 600) <= 1.5


class _Client:
    """Fake Twelve Labs: records uploads and deletes."""

    def __init__(self, answer):
        self.answer = answer
        self.deleted = []
        self.uploads = 0
        self.assets = self

    def create(self, method, file):
        self.uploads += 1
        return SimpleNamespace(id=f"a{self.uploads}")

    def retrieve(self, asset_id):
        return SimpleNamespace(status="ready")

    def delete(self, asset_id):
        self.deleted.append(asset_id)

    def analyze(self, **kwargs):
        return SimpleNamespace(data=self.answer)


def _scene(monkeypatch, tmp_path):
    pytest.importorskip("twelvelabs")
    source = tmp_path / "stream.mp4"
    source.write_bytes(b"x")

    def proxy(src, start, length, out, video_kbps=250):
        with open(out, "wb") as handle:
            handle.write(b"small")
        return True

    monkeypatch.setattr(tl, "_proxy", proxy)
    monkeypatch.setattr(tl.shutil, "which", lambda name: "/bin/ffmpeg")
    return str(source)


def test_every_upload_is_deleted_and_the_minutes_counted(monkeypatch,
                                                         tmp_path):
    source = _scene(monkeypatch, tmp_path)
    client = _Client(json.dumps({"moments": [
        {"start": 60, "end": 90, "score": 0.7}]}))
    logs = str(tmp_path / "logs")

    found = tl.find_moments(source, 2 * 3600, logs, client=client,
                            say=lambda *a: None)

    assert client.uploads == 3
    assert client.deleted == ["a1", "a2", "a3"]
    assert len(found) == 3
    assert tl.minutes_used(logs) == pytest.approx(120, abs=0.2)


def test_nothing_is_sent_once_the_allowance_is_spent(monkeypatch, tmp_path):
    """The free plan is 600 minutes in TOTAL - three streams."""
    source = _scene(monkeypatch, tmp_path)
    client = _Client("{}")
    logs = str(tmp_path / "logs")
    os.makedirs(logs)
    with open(os.path.join(logs, "twelvelabs_usage.json"), "w") as handle:
        json.dump({"minutes": 550}, handle)

    assert tl.find_moments(source, 3 * 3600, logs, client=client,
                           say=lambda *a: None) == []
    assert client.uploads == 0


def test_no_key_means_no_opinion(monkeypatch, tmp_path):
    monkeypatch.delenv(tl.KEY_NAME, raising=False)
    source = tmp_path / "s.mp4"
    source.write_bytes(b"x")
    assert tl.find_moments(str(source), 3600) == []


def test_the_scorer_lifts_what_twelve_labs_picked():
    from autoreel.highlights import HighlightScorer

    plain = HighlightScorer()
    seen = HighlightScorer(seen=tl.seen_curve([(0, 30, 1.0, "")], 60))
    assert seen.seen and not plain.seen

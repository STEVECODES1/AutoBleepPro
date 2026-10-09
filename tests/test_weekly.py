"""The weekly best-of for STACKSWOPOVODS, built from kept recaps."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils import weekly  # noqa: E402

NOW = dt.datetime(2026, 10, 15, 20, 0)


def _cfg(tmp_path):
    for d in ("censored", "logs"):
        (tmp_path / d).mkdir(exist_ok=True)
    return SimpleNamespace(project_root=str(tmp_path), general=SimpleNamespace(
        censored_folder=str(tmp_path / "censored"), logs_folder=str(tmp_path / "logs")))


def _recap(cfg, tmp_path, name, days_ago, title="THOTBREAKER", moments=None):
    src = tmp_path / name
    src.write_bytes(b"x")
    return weekly.keep(cfg, str(src), title, "10-08-26", "https://youtu.be/r", "",
                       moments or [(0, 60, 0.5), (60, 120, 0.9)],
                       now=NOW - dt.timedelta(days=days_ago))


def test_keep_moves_the_recap_and_writes_its_moments(tmp_path):
    cfg = _cfg(tmp_path)
    dest = _recap(cfg, tmp_path, "a_RECAP.mp4", 1)
    assert os.path.exists(dest) and not (tmp_path / "a_RECAP.mp4").exists()
    info = weekly.recaps(cfg)[0]
    assert info["moments"][1] == [60.0, 120.0, 0.9]


def test_not_due_until_the_oldest_recap_is_about_a_week_old(tmp_path):
    cfg = _cfg(tmp_path)
    _recap(cfg, tmp_path, "a_RECAP.mp4", 3)
    _recap(cfg, tmp_path, "b_RECAP.mp4", 1)
    assert weekly.due(cfg, NOW)[0] == []
    _recap(cfg, tmp_path, "c_RECAP.mp4", 6.5)
    window, stale = weekly.due(cfg, NOW)
    assert [r["video"] for r in window] == ["c_RECAP.mp4", "a_RECAP.mp4", "b_RECAP.mp4"]
    assert stale == []


def test_old_and_used_recaps_are_left_out(tmp_path):
    cfg = _cfg(tmp_path)
    _recap(cfg, tmp_path, "old_RECAP.mp4", 10)
    _recap(cfg, tmp_path, "a_RECAP.mp4", 7)
    _recap(cfg, tmp_path, "b_RECAP.mp4", 2)
    window, stale = weekly.due(cfg, NOW)
    assert [r["video"] for r in stale] == ["old_RECAP.mp4"]
    weekly._mark_used(cfg, window + stale)
    assert weekly.due(cfg, NOW) == ([], [])


def test_best_moments_win_and_play_in_stream_order():
    a = {"video": "a", "moments": [[0, 100, 0.2], [100, 200, 0.9]]}
    b = {"video": "b", "moments": [[0, 100, 0.8], [100, 103, 1.0]]}   # 3 s: too short
    got = weekly.pick([a, b], max_s=200)
    assert [(r["video"], s) for r, s, _, _ in got] == [("a", 100), ("b", 0)]


def test_chapters_only_when_youtube_would_take_them():
    assert weekly.chapters([(0, "A"), (30, "A"), (70, "B")]) == ""      # only 2
    assert weekly.chapters([(0, "A"), (70, "B"), (75, "C")]) == ""      # 5 s chapter
    assert weekly.chapters([(0, "A"), (70, "B"), (3700, "C")]) == "0:00 A\n1:10 B\n1:01:40 C"


def test_strangers_on_camera_titles_are_risky():
    assert weekly.risky("TROLLING ON MONKEY APP")
    assert weekly.risky("omegle night")
    assert not weekly.risky("THOTBREAKER")


def test_prune_drops_recaps_older_than_two_weeks(tmp_path):
    cfg = _cfg(tmp_path)
    _recap(cfg, tmp_path, "old_RECAP.mp4", 15)
    _recap(cfg, tmp_path, "new_RECAP.mp4", 3)
    assert weekly.prune(cfg, NOW) == 1
    assert [r["video"] for r in weekly.recaps(cfg)] == ["new_RECAP.mp4"]


def test_weekly_uploads_private_when_a_stream_had_strangers(tmp_path, monkeypatch):
    import utils.recap as recap

    cfg = _cfg(tmp_path)
    (tmp_path / "youtube_compilation_token.json").write_text("{}")
    long = [(i * 100.0, i * 100.0 + 90, 0.5) for i in range(4)]
    _recap(cfg, tmp_path, "a_RECAP.mp4", 7, "GTA RP", long)
    _recap(cfg, tmp_path, "b_RECAP.mp4", 1, "MONKEY APP", long)
    sent = {}
    monkeypatch.setattr(recap, "build_from",
                        lambda items, out, vf="": [(i * 90.0, i * 90.0 + 90, i)
                                                   for i in range(len(items))])
    monkeypatch.setattr(recap, "_links_block", lambda cfg: "ALL LINKS")
    monkeypatch.setattr(recap, "upload", lambda *a, **k: sent.update(args=a) or "https://y/1")
    assert weekly.maybe_make(cfg, NOW) == "https://y/1"
    title, description, privacy = sent["args"][2], sent["args"][3], sent["args"][5]
    assert title == "Stackswopo - BEST OF THE WEEK (10-08-26)"
    assert privacy == "private"
    assert "https://www.youtube.com/@wopovod" in description
    led = json.load(open(tmp_path / "logs" / "weekly.json", encoding="utf-8"))
    assert led["used"] == ["a_RECAP.mp4", "b_RECAP.mp4"]
    assert weekly.maybe_make(cfg, NOW) == ""          # never twice

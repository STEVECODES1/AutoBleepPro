import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from utils import hang_watch  # noqa: E402


def _beat(tmp_path, monkeypatch, age_s, pid):
    p = tmp_path / "beat.json"
    p.write_text(json.dumps({"pid": pid, "at": time.time() - age_s}))
    monkeypatch.setattr(hang_watch, "BEAT", str(p))
    monkeypatch.setattr(hang_watch, "UPLOADING", str(tmp_path / "none.json"))
    monkeypatch.setattr(hang_watch, "_last_kick", 0.0)


def test_fresh_beat_is_fine(tmp_path, monkeypatch):
    _beat(tmp_path, monkeypatch, 60, os.getpid())
    monkeypatch.setattr(hang_watch, "_pid_alive", lambda pid: True)
    assert hang_watch.check(say=lambda *_: None) is False


def test_stale_beat_with_live_pid_kicks_a_restart(tmp_path, monkeypatch):
    _beat(tmp_path, monkeypatch, 20 * 60, 12345)
    monkeypatch.setattr(hang_watch, "_pid_alive", lambda pid: True)
    calls = []
    monkeypatch.setattr(hang_watch.subprocess, "Popen", lambda *a, **k: calls.append(a))
    assert hang_watch.check(say=lambda *_: None) is True
    assert calls and "quiet_restart.ps1" in calls[0][0][4]


def test_stale_beat_dead_pid_does_nothing(tmp_path, monkeypatch):
    _beat(tmp_path, monkeypatch, 20 * 60, 12345)
    monkeypatch.setattr(hang_watch, "_pid_alive", lambda pid: False)
    assert hang_watch.check(say=lambda *_: None) is False

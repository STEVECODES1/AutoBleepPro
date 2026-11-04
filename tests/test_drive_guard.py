"""USB drive reconnect recovery (utils/drive_guard.py, 11-03)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from utils import drive_guard  # noqa: E402


def test_a_healthy_folder_probes_clean(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert drive_guard._probe(str(tmp_path)) == 0
    assert not list(tmp_path.iterdir())          # the probe file is gone again


def test_a_stale_volume_is_reported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def boom(*a, **k):
        err = OSError("A device which does not exist was specified")
        err.winerror = 433
        raise err

    monkeypatch.setattr("builtins.open", boom)
    assert drive_guard._probe(str(tmp_path)) == 433


def test_other_errors_are_not_a_reconnect(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def denied(*a, **k):
        raise PermissionError("no")

    monkeypatch.setattr("builtins.open", denied)
    assert drive_guard._probe(str(tmp_path)) == 0


def test_chdir_mode_reenters_and_does_not_exit(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    exits = []
    monkeypatch.setattr(drive_guard.os, "_exit", lambda code: exits.append(code))
    drive_guard._heal(str(tmp_path), "chdir", say=lambda s: None)
    assert os.getcwd() == str(tmp_path) and exits == []


def test_exit_mode_exits_for_the_keepalive(tmp_path, monkeypatch):
    exits = []
    monkeypatch.setattr(drive_guard.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(drive_guard.time, "sleep", lambda s: None)
    drive_guard._heal(str(tmp_path), "exit", say=lambda s: None)
    assert exits == [drive_guard.EXIT_CODE]

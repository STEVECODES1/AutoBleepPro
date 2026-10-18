"""10-18: the owner runs this from a phone, away from the PC. The watchdog
now also notices the Rumble Chrome gone, YouTube refusing uploads, a full
stream stuck in the watch folder, a clip post stuck 'in progress' - and
sends one daily report of what went up."""

from __future__ import annotations

import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils import brain as brain_mod  # noqa: E402
from utils.brain import Brain  # noqa: E402

T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "watch").mkdir()
    (tmp_path / "logs").mkdir()
    return SimpleNamespace(
        project_root=str(tmp_path), features={},
        general=SimpleNamespace(watch_folder=str(tmp_path / "watch"),
                                logs_folder=str(tmp_path / "logs")))


@pytest.fixture(autouse=True)
def plenty_of_disk(monkeypatch):
    monkeypatch.setattr(brain_mod, "disk_free_gb", lambda p: 500.0)


def make(cfg, clock, chrome=None):
    sent = []

    def send(title, message, urgent=True):
        sent.append((title, message, urgent))
        return ["test"]

    b = Brain(cfg, None, send=send, live_check=lambda url: None,
              chrome_check=chrome, now=clock, say=lambda *_: None)
    return b, sent


def test_a_closed_rumble_chrome_is_said_after_five_minutes_not_at_once(cfg):
    clock = Clock()
    b, sent = make(cfg, clock, chrome=lambda: False)
    assert "chrome_down" not in b.tick()
    clock.t += 6 * 60
    assert "chrome_down" in b.tick()
    assert sent[-1][0] == "Rumble Chrome is closed" and sent[-1][2] is True


def test_youtube_refusing_uploads_is_said(cfg):
    clock = Clock()
    with open(os.path.join(cfg.general.logs_folder, "youtube_blocked.json"), "w") as f:
        json.dump({"until": T0 + 3600}, f)
    b, _ = make(cfg, clock)
    assert "youtube_blocked" in b.problems()
    clock.t += 7200
    assert "youtube_blocked" not in b.problems()


def test_a_full_stream_stuck_for_hours_is_said_unless_an_upload_is_moving(cfg, monkeypatch):
    monkeypatch.setattr(brain_mod, "BIG_VIDEO_BYTES", 10)
    video = os.path.join(cfg.general.watch_folder, "STREAM.ts")
    with open(video, "wb") as f:
        f.write(b"x" * 100)
    old = T0 - 7 * 3600
    os.utime(video, (old, old))
    clock = Clock()
    b, _ = make(cfg, clock)
    assert "stuck_stream:STREAM.ts" in b.problems()
    beat = os.path.join(cfg.general.logs_folder, "uploading.json")
    with open(beat, "w") as f:
        f.write("{}")
    os.utime(beat, (T0 - 60, T0 - 60))
    assert not any(k.startswith("stuck_stream") for k in b.problems())


def test_a_clip_post_stuck_in_progress_is_said(cfg):
    with open(os.path.join(cfg.project_root, "clip_jobs.json"), "w") as f:
        json.dump({"jobs": {"a": {"platform": "instagram", "state": "in_progress",
                                  "claimed_at": T0 - 3 * 3600, "clip_path": "x/clip.mp4"}}}, f)
    b, _ = make(cfg, Clock())
    assert "stuck_clip" in b.problems()


def test_the_daily_report_says_what_went_up_once_a_day(cfg):
    with open(os.path.join(cfg.general.logs_folder, "stream_links.json"), "w") as f:
        json.dump([{"title": "EXPOSED", "date": "10-17-26", "at": T0 - 3600,
                    "youtube": "https://www.youtube.com/watch?v=abc",
                    "rumble": "https://rumble.com/v1-x.html"}], f)
    with open(os.path.join(cfg.project_root, "clip_jobs.json"), "w") as f:
        json.dump({"jobs": {
            "1": {"platform": "youtube_shorts", "state": "done", "updated_at": T0 - 60},
            "2": {"platform": "instagram", "state": "done", "updated_at": T0 - 60},
            "3": {"platform": "facebook", "state": "failed", "updated_at": T0 - 60,
                  "last_error": "held: 3 slurs"},
        }}, f)
    local = time.localtime(T0)
    clock = Clock(T0 + (12 - local.tm_hour) * 3600)  # midday, local time
    b, sent = make(cfg, clock)
    assert b.daily_digest() is True
    title, text, urgent = sent[-1]
    assert title == "Daily report" and urgent is False
    assert "EXPOSED" in text and "https://www.youtube.com/watch?v=abc" in text
    assert "Shorts 1" in text and "IG 1" in text and "Held back by the guards: 1" in text
    assert b.daily_digest() is False   # once a day

"""The watchdog: notices when the pipeline has quietly stopped, and says so
once - not every minute, and not to a desktop nobody is sitting at."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils import brain as brain_mod  # noqa: E402
from utils.brain import Brain, send_alert  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def cfg(tmp_path):
    return SimpleNamespace(
        project_root=str(tmp_path),
        features={},
        general=SimpleNamespace(watch_folder=str(tmp_path / "watch"),
                                logs_folder=str(tmp_path / "logs")))


@pytest.fixture(autouse=True)
def plenty_of_disk(monkeypatch):
    monkeypatch.setattr(brain_mod, "disk_free_gb", lambda p: 500.0)


def make(cfg, clock, live=None, **settings):
    sent = []

    def send(title, message, urgent=True):
        sent.append((title, urgent))
        return ["test"]

    b = Brain(cfg, settings or None, send=send,
              live_check=live or (lambda url: None),
              now=clock, say=lambda *_: None)
    return b, sent


def beat(cfg, clock, state="waiting", size=0, name="Stackswopo"):
    folder = os.path.join(cfg.project_root, "recording", ".heartbeat")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name + ".json"), "w") as handle:
        json.dump({"time": clock(), "name": name, "state": state,
                   "bytes": size,
                   "url": "https://www.youtube.com/@stackswopo_/live"},
                  handle)


def test_no_heartbeat_ever_is_not_an_alarm(cfg):
    clock = Clock()
    b, sent = make(cfg, clock)
    assert b.tick() == {}
    assert sent == []


def test_a_silent_recorder_is_reported_once_then_fixed(cfg):
    clock = Clock()
    b, sent = make(cfg, clock)
    beat(cfg, clock)
    clock.t += 6 * 60
    assert "recorder_down" in b.tick()
    clock.t += 60
    b.tick()
    assert [t for t, _ in sent] == ["Recorder is not running"], \
        "repeats are muted"
    beat(cfg, clock)
    b.tick()
    assert sent[-1] == ("Fixed: Recorder is not running", False)


def test_a_muted_problem_is_reminded_after_mute_hours(cfg):
    clock = Clock()
    b, sent = make(cfg, clock)
    beat(cfg, clock)
    clock.t += 6 * 60
    b.tick()
    clock.t += 6 * 3600 + 1
    b.tick()
    assert [t for t, _ in sent] == ["Recorder is not running",
                                    "Still: Recorder is not running"]


def test_open_problems_survive_a_restart(cfg):
    clock = Clock()
    b, sent = make(cfg, clock)
    beat(cfg, clock)
    clock.t += 6 * 60
    b.tick()
    again, sent_again = make(cfg, clock)
    again.tick()
    assert sent_again == [], "already told - a restart must not re-send"


def test_a_recording_that_stops_growing_is_stuck(cfg):
    clock = Clock()
    b, sent = make(cfg, clock)
    for _ in range(4):
        beat(cfg, clock, "recording", 1000)
        assert not b.tick()
        clock.t += 60
    beat(cfg, clock, "recording", 1000)
    clock.t += 60
    beat(cfg, clock, "recording", 1000)
    assert "stalled:Stackswopo" in b.tick()


def test_a_growing_recording_is_fine(cfg):
    clock = Clock()
    b, sent = make(cfg, clock)
    for i in range(20):
        beat(cfg, clock, "recording", 1000 * (i + 1))
        assert not b.tick()
        clock.t += 60
    assert sent == []


def test_live_but_waiting_needs_two_checks_an_interval_apart(cfg):
    clock = Clock()
    b, sent = make(cfg, clock, live=lambda url: True)
    beat(cfg, clock)
    assert not b.tick()             # first sighting
    clock.t += 5 * 60
    beat(cfg, clock)
    assert not b.tick()             # a start takes seconds, not minutes
    clock.t += 5 * 60
    beat(cfg, clock)
    assert "live_not_recording:Stackswopo" in b.tick()


def test_live_and_recording_is_not_a_problem(cfg):
    clock = Clock()
    b, sent = make(cfg, clock, live=lambda url: True)
    for i in range(15):
        beat(cfg, clock, "recording", 10 * (i + 1))
        b.tick()
        clock.t += 60
    assert sent == []


def test_history_problems_are_not_fixed_by_a_restart(cfg):
    clock = Clock()
    b, _ = make(cfg, clock, live=lambda url: True)
    beat(cfg, clock)
    b.tick()
    clock.t += 10 * 60
    beat(cfg, clock)
    assert b.tick()
    again, sent = make(cfg, clock, live=lambda url: True)
    beat(cfg, clock)
    again.tick()
    assert sent == [], "no memory yet is not the same as fixed"


def test_low_disk(cfg, monkeypatch):
    monkeypatch.setattr(brain_mod, "disk_free_gb", lambda p: 3.0)
    b, sent = make(cfg, Clock())
    assert "low_disk" in b.tick()


def test_crash_loop(cfg):
    clock = Clock()
    for _ in range(5):
        b, sent = make(cfg, clock)
        b.on_start()
        clock.t += 60
    assert "crash_loop" in b.tick()
    clock.t += 31 * 60
    b.tick()
    assert sent[-1] == ("Fixed: Uploader keeps crashing", False)


def test_disabled_does_nothing(cfg, monkeypatch):
    monkeypatch.setattr(brain_mod, "disk_free_gb", lambda p: 1.0)
    b, sent = make(cfg, Clock(), enabled=False)
    assert b.tick() == {} and sent == []


def test_a_broken_send_never_raises(cfg):
    clock = Clock()

    def explode(*a, **k):
        raise RuntimeError("network")

    b = Brain(cfg, send=explode, live_check=lambda u: None, now=clock,
              say=lambda *_: None)
    beat(cfg, clock)
    clock.t += 6 * 60
    b.tick()


# ── where alerts go ─────────────────────────────────────────────────────────

@pytest.fixture
def env(monkeypatch):
    for name in ("DISCORD_ALERT_WEBHOOK_URL", "DISCORD_JOB_WEBHOOK_URL",
                 "DISCORD_WEBHOOK_URL", "DISCORD_ALERT_USER_ID",
                 "NTFY_TOPIC", "NTFY_SERVER"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def capture():
    calls = []

    def post(url, body, headers):
        calls.append((url, body, headers))
        return True
    return calls, post


def test_nothing_configured_sends_nothing(env):
    calls, post = capture()
    assert send_alert("t", "m", post=post) == []
    assert calls == []


def test_urgent_discord_alert_mentions_only_the_operator(env):
    env.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    env.setenv("DISCORD_ALERT_USER_ID", "1234")
    calls, post = capture()
    assert send_alert("Recorder down", "msg", post=post) == ["discord"]
    payload = json.loads(calls[0][1])
    assert payload["content"].startswith("<@1234> ")
    assert payload["allowed_mentions"] == {"users": ["1234"]}


def test_a_fixed_message_does_not_ping(env):
    env.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    env.setenv("DISCORD_ALERT_USER_ID", "1234")
    calls, post = capture()
    send_alert("Fixed", "ok", urgent=False, post=post)
    payload = json.loads(calls[0][1])
    assert "<@" not in payload["content"]
    assert payload["allowed_mentions"] == {"parse": []}


def test_ntfy_push(env):
    env.setenv("NTFY_TOPIC", "wopo-alerts")
    calls, post = capture()
    assert send_alert("Disk almost full 💾", "3 GB", post=post) == ["ntfy"]
    url, body, headers = calls[0]
    assert url == "https://ntfy.sh/wopo-alerts"
    assert body == b"3 GB"
    assert headers["Priority"] == "high"
    headers["Title"].encode("latin-1")   # an emoji must not break the header

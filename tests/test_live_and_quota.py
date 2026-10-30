"""10-30: nothing uploads while he is live; wopovod always keeps quota."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "auto_uploader"))
from utils import live_now, quota  # noqa: E402


def _beat(folder, state, age=0):
    d = folder / ".heartbeat"
    d.mkdir(exist_ok=True)
    (d / "Stackswopo.json").write_text(json.dumps({"time": time.time() - age, "state": state}))


def test_live_only_while_a_fresh_heartbeat_says_recording(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _beat(tmp_path, "recording")
    assert live_now.is_live(str(tmp_path))
    _beat(tmp_path, "waiting")
    assert not live_now.is_live(str(tmp_path))
    _beat(tmp_path, "recording", age=600)       # a dead recorder never blocks forever
    assert not live_now.is_live(str(tmp_path))


def test_work_waits_until_the_stream_ends():
    states = iter([True, True, False])
    slept = []
    waited = live_now.wait_until_off_air("x", poll_s=5, sleep=slept.append,
                                         live=lambda: next(states))
    assert waited == 10 and slept == [5, 5]


def test_extras_stop_while_wopovod_still_has_its_upload(tmp_path):
    path = str(tmp_path / "q.json")
    for _ in range(4):
        quota.record(quota.INSERT, "short", path=path)       # 6,400
    assert quota.allow_extra(path=path)                       # 6,400+1,600+1,600 = 9,600
    quota.record(quota.INSERT, "recap", path=path)            # 8,000
    assert not quota.allow_extra(path=path)                   # would leave < 1,600
    assert quota.spent(path=path) == 8000


def test_every_extra_upload_path_asks_the_quota_first():
    root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "auto_uploader", "utils")
    for name in ("recap.py", "stories.py"):
        src = open(os.path.join(root, name), encoding="utf-8").read()
        i = src.index("videos().insert(")
        assert "allow_extra()" in src[i - 600:i], name
    src = open(os.path.join(root, "clip_queue.py"), encoding="utf-8").read()
    assert "allow_extra()" in src and "is_live()" in src


def test_a_reel_copy_is_made_to_instagram_spec(tmp_path):
    import shutil, subprocess
    if not shutil.which("ffmpeg"):
        return
    from publishers.instagram import reel_spec_copy
    src = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=60:d=2",
                    "-f", "lavfi", "-i", "sine=d=2", "-shortest", str(src)], check=True)
    out = reel_spec_copy(str(src))
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,r_frame_rate", "-of", "csv=p=0", out],
                           capture_output=True, text=True).stdout.strip()
    assert probe == "1080,1920,30/1"


def test_the_brain_says_when_chrome_keeps_crashing(tmp_path, monkeypatch):
    from utils import brain
    for i in range(6):
        (tmp_path / f"{i}.dmp").write_bytes(b"x")
    monkeypatch.setenv("AUTOBLEEP_CHROME_CRASHES", str(tmp_path))
    b = brain.Brain.__new__(brain.Brain)
    got = brain.Brain._chrome_crashing(b, time.time())
    assert "chrome_crashing" in got
    monkeypatch.setenv("AUTOBLEEP_CHROME_CRASHES", str(tmp_path / "none"))
    assert brain.Brain._chrome_crashing(b, time.time()) == {}

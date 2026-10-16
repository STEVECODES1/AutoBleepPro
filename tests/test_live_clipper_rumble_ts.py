"""The live clipper reads the Rumble recorder's growing MPEG-TS too.

Since 10-13 Stackswopo streams on Rumble; tools/rumble_watch.py records
"Stackswopo rumble <id> fromstart.ts" from the DVR's first segment. Clips
must come out of that while he is still live, the same as from YouTube's
fragments."""
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "auto_uploader"))

from utils import live_clipper as lc  # noqa: E402


def _ts(path, seconds=6):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-g", "30", "-c:a", "aac", "-f", "mpegts", str(path)],
                   check=True, capture_output=True)


def test_growing_ts_is_found_and_named(tmp_path):
    path = tmp_path / "Stackswopo rumble v7abc fromstart.ts"
    path.write_bytes(b"x")
    (tmp_path / "Stackswopo rumble v7abc fromstart.txt").write_text("exposing 2026-10-15 19:26\n")
    (tmp_path / "Stackswopo rumble live 2026-10-15 19_49.ts.part").write_bytes(b"x")  # the backup capture
    recs = lc.find_live(str(tmp_path))
    assert len(recs) == 1 and recs[0].ts_path == str(path)
    assert recs[0].key.startswith("Stackswopo rumble live ")
    assert lc.stream_title(recs[0]) == "exposing"


def test_a_stopped_ts_is_not_live(tmp_path):
    path = tmp_path / "Stackswopo rumble v7abc fromstart.ts"
    path.write_bytes(b"x")
    old = time.time() - lc.ACTIVE_S - 60
    os.utime(path, (old, old))
    assert lc.find_live(str(tmp_path)) == []


def test_no_lag_hold_for_a_ts(tmp_path, monkeypatch):
    (tmp_path / "Stackswopo rumble v7abc fromstart.ts").write_bytes(b"x")
    monkeypatch.setattr(lc, "recording_folder", lambda cfg: str(tmp_path))
    assert lc.recording_lag(None) == 0.0


@pytest.mark.skipif(subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode != 0,
                    reason="no ffmpeg")
def test_a_window_is_cut_from_the_ts(tmp_path):
    path = tmp_path / "Stackswopo rumble v7abc fromstart.ts"
    _ts(path, 6)
    rec = lc.find_live(str(tmp_path))[0]
    assert lc.edge_seconds(rec) == 0.0           # 6 s of stream, 15 s safety margin
    out = tmp_path / "w.mp4"
    assert lc.cut_window(rec, 1.0, 4.0, str(out)) == 1.0
    assert out.stat().st_size > 0

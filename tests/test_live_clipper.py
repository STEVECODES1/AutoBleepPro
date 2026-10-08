"""Clips cut while the stream is still live (utils/live_clipper)."""
import os
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from utils import live_clipper as L  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="needs ffmpeg")
BASE = "Stackswopo youtube live 2026-10-08 19_00.part01.ts"


@pytest.fixture(autouse=True)
def _fresh_state():
    L._KINDS.clear()
    L._MISSES.clear()
    yield


def _cfg(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    return SimpleNamespace(
        project_root=str(tmp_path),
        clips={"count": 20, "live_work_folder": str(tmp_path / "live_windows")},
        general=SimpleNamespace(logs_folder=str(logs)))


def _clip(path, start, end):
    path.write_bytes(b"clip")
    return SimpleNamespace(path=str(path),
                           spec=SimpleNamespace(start=start, end=end))


def _live(monkeypatch, tmp_path, seconds):
    """A recording `seconds` into the stream; reading a window works."""
    rec = L.LiveRecording(BASE, str(tmp_path), video={1: str(tmp_path / "v")},
                          audio={1: str(tmp_path / "a")}, newest=time.time())
    monkeypatch.setattr(L, "find_live", lambda folder, now=None: [rec])
    monkeypatch.setattr(L, "edge_seconds", lambda rec: float(seconds))

    def cut(rec, start, end, out):
        open(out, "wb").close()
        return float(start)

    monkeypatch.setattr(L, "cut_window", cut)
    return rec


def test_nothing_is_clipped_until_a_whole_window_has_streamed(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path, L.WINDOW_S - 60)
    asked = []
    L.tick(_cfg(tmp_path), deliver=lambda run: 0, folder=str(tmp_path),
           make_clips=lambda *a, **k: asked.append(a))
    assert asked == []


def test_a_window_is_clipped_and_its_clips_delivered(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path, L.WINDOW_S + 100)
    windows, delivered = [], []

    def make(cfg, window, title, **kw):
        windows.append((os.path.basename(window), cfg.clips))
        return SimpleNamespace(clips=[_clip(tmp_path / "c1.mp4", 300, 330)],
                               skipped_reason="")

    got = L.tick(_cfg(tmp_path), deliver=lambda run: delivered.append(run)
                 or len(run.clips), folder=str(tmp_path), make_clips=make)
    assert got == 1 and len(delivered) == 1
    name, clips_cfg = windows[0]
    # Named after the recording (its date), numbered per window.
    assert name == "Stackswopo youtube live 2026-10-08 19_00 LIVE 01.mp4"
    # The window file is scratch: gone once clipped.
    assert not (tmp_path / "live_windows" / name).exists()
    # First window: the waiting screen is skipped; live-only signals off.
    assert clips_cfg["skip_intro_seconds"] == L.SKIP_OPENING_S
    assert clips_cfg["use_twelvelabs"] is False
    assert clips_cfg["count"] == 20          # the rest of cfg.clips kept
    ledger = L.load_ledger(str(tmp_path / "logs"))
    entry = ledger[BASE]
    assert entry["windows"] == 1
    assert entry["read_until"] == pytest.approx(L.WINDOW_S + 100)
    assert entry["clipped"] == [[300, 330]]


def test_the_next_window_overlaps_and_never_clips_a_moment_twice(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    logs = str(tmp_path / "logs")
    L.save_ledger(logs, {BASE: {"title": "YURRRR WTW", "read_until": 720.0,
                                "windows": 1, "clipped": [[700, 715]],
                                "updated": time.time()}})
    _live(monkeypatch, tmp_path, 720 + L.WINDOW_S + 40)
    seen, configs, delivered = [], [], []

    def make(cfg, window, title, **kw):
        seen.append(title)
        configs.append(cfg.clips)
        # Clip times are relative to the window, which starts 45 s back.
        return SimpleNamespace(clips=[
            _clip(tmp_path / "dup.mp4", 25, 40),     # = 700-715, done
            _clip(tmp_path / "new.mp4", 200, 230)], skipped_reason="")

    L.tick(cfg, deliver=lambda run: delivered.append(run) or len(run.clips),
           folder=str(tmp_path), make_clips=make)
    assert seen == ["YURRRR WTW"]
    # Later windows start mid-stream: nothing to skip at their start.
    assert configs[0]["skip_intro_seconds"] == 0
    assert [os.path.basename(c.path) for c in delivered[0].clips] == ["new.mp4"]
    assert not (tmp_path / "dup.mp4").exists()
    entry = L.load_ledger(logs)[BASE]
    start = 720.0 - L.OVERLAP_S
    assert [start + 200, start + 230] in entry["clipped"]
    assert entry["windows"] == 2


def test_a_late_join_reads_in_bounded_windows(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path, 3 * 3600)
    spans = []
    monkeypatch.setattr(L, "clip_window",
                        lambda cfg, rec, entry, start, end, *a, **k:
                        spans.append((start, end)) or 0)
    L.tick(_cfg(tmp_path), deliver=lambda run: 0, folder=str(tmp_path))
    assert spans == [(0.0, L.MAX_WINDOW_S)]
    # ...and the next pass carries on from there.
    L.tick(_cfg(tmp_path), deliver=lambda run: 0, folder=str(tmp_path))
    assert spans[1][0] == L.MAX_WINDOW_S - L.OVERLAP_S


def test_a_failed_read_is_retried_then_skipped(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path, L.WINDOW_S + 100)
    monkeypatch.setattr(L, "cut_window", lambda *a: None)
    logs = str(tmp_path / "logs")
    for attempt in range(L.MAX_MISSES - 1):
        L.tick(_cfg(tmp_path), deliver=lambda run: 0, folder=str(tmp_path),
               make_clips=lambda *a, **k: pytest.fail("nothing was read"))
        assert L.load_ledger(logs) == {}
    # Not forever: the stretch is passed over (the VOD pass has it).
    L.tick(_cfg(tmp_path), deliver=lambda run: 0, folder=str(tmp_path),
           make_clips=lambda *a, **k: pytest.fail("nothing was read"))
    assert L.load_ledger(logs)[BASE]["read_until"] == L.WINDOW_S + 100


def test_a_clip_run_that_crashes_moves_on(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path, L.WINDOW_S + 100)

    def boom(*a, **k):
        raise RuntimeError("model fell over")

    assert L.tick(_cfg(tmp_path), deliver=lambda run: 0, folder=str(tmp_path),
                  make_clips=boom) == 0
    entry = L.load_ledger(str(tmp_path / "logs"))[BASE]
    assert entry["read_until"] == L.WINDOW_S + 100 and entry["clipped"] == []
    assert os.listdir(tmp_path / "live_windows") == []


def test_the_vod_pass_finds_what_was_clipped_live(tmp_path):
    logs = str(tmp_path)
    now = time.time()
    L.save_ledger(logs, {BASE: {"title": "YURRRR WTW", "read_until": 9000,
                                "clipped": [[100, 130]], "updated": now}})
    by_name = L.clipped_ranges_for(
        logs, r"D:\x\Stackswopo youtube live 2026-10-08 19_00.part01.ts", now=now)
    by_title = L.clipped_ranges_for(
        logs, r"D:\x\'YURRRR WTW' 10-8-26 Stackswopo Stream.ts", now=now)
    by_time = L.clipped_ranges_for(logs, r"D:\x\renamed.ts", now=now,
                                   duration=9500)
    assert by_name == by_title == by_time == [(100, 130)]


def test_an_old_or_different_vod_is_not_matched(tmp_path):
    logs = str(tmp_path)
    now = time.time()
    L.save_ledger(logs, {BASE: {"title": "YURRRR WTW", "read_until": 9000,
                                "clipped": [[100, 130]], "updated": now}})
    # A short unrelated video, and anything a day later.
    assert L.clipped_ranges_for(logs, "other.ts", now=now, duration=600) == []
    assert L.clipped_ranges_for(logs, "other.ts", now=now + 86400,
                                duration=9500) == []


def test_windows_are_built_off_the_recording_drive_and_leftovers_swept(tmp_path):
    cfg = SimpleNamespace(clips={})
    assert L.work_folder(cfg).startswith(__import__("tempfile").gettempdir())
    old, fresh = tmp_path / "old LIVE 03.mp4", tmp_path / "now LIVE 04.mp4"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    os.utime(old, (time.time() - 4 * 3600,) * 2)
    L._sweep(str(tmp_path))
    assert not old.exists() and fresh.exists()


def test_parallel_reads_keep_the_fragment_order(tmp_path):
    paths = []
    for n in range(200):
        path = tmp_path / f"f{n}"
        path.write_bytes(n.to_bytes(2, "big") * 3)
        paths.append(str(path))
    out = tmp_path / "joined"
    assert L._join(paths, str(out))
    assert out.read_bytes() == b"".join(n.to_bytes(2, "big") * 3
                                        for n in range(200))


def test_overlap_is_measured_against_the_shorter_clip():
    assert L.is_already_clipped((10, 20), [(15, 60)])
    assert not L.is_already_clipped((10, 20), [(19, 60)])


def test_the_vod_pass_waits_for_a_live_window_in_progress(monkeypatch):
    import threading

    done = threading.Event()
    worker = threading.Thread(target=lambda: done.wait(0.3))
    worker.start()
    monkeypatch.setitem(L._WORKER, "thread", worker)
    assert L.wait_idle(timeout=5) is True
    assert not worker.is_alive()


def _dash_fragments(folder, fmt, lavfi, codec):
    """Fragments laid out like yt-dlp's live ones: every file is the init
    (ftyp+moov) plus about a second of moof+mdat, carrying its real place
    in the stream - the layout measured on a live recording."""
    whole = folder / f"whole{fmt}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", lavfi,
                    *codec, "-frag_duration", "1000000", "-f", "mp4",
                    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                    str(whole)], check=True)
    data = whole.read_bytes()
    boxes, i = [], 0
    while i + 8 <= len(data):
        size = int.from_bytes(data[i:i + 4], "big")
        boxes.append((data[i + 4:i + 8], data[i:i + size]))
        i += size
    head = b"".join(b for kind, b in boxes if kind in (b"ftyp", b"moov"))
    body = [b for kind, b in boxes if kind in (b"moof", b"mdat")]
    for n, k in enumerate(range(0, len(body) - 1, 2), 1):
        (folder / f"{BASE}.f{fmt}.mp4.part-Frag{n}").write_bytes(
            head + body[k] + body[k + 1])
    whole.unlink()


def _streams(path):
    import json

    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type", "-of", "json",
                          str(path)], capture_output=True, text=True).stdout
    data = json.loads(out)
    return (sorted(s["codec_type"] for s in data["streams"]),
            float(data["format"]["duration"]))


@needs_ffmpeg
def test_live_fragments_are_found_paired_and_joined(tmp_path):
    _dash_fragments(tmp_path, "299", "testsrc2=s=320x240:r=30:d=10",
                    ["-c:v", "libx264", "-g", "30", "-keyint_min", "30",
                     "-sc_threshold", "0", "-pix_fmt", "yuv420p"])
    _dash_fragments(tmp_path, "140", "sine=frequency=440:duration=10",
                    ["-c:a", "aac"])
    # One still arriving: yt-dlp's in-progress name, never read.
    (tmp_path / f"{BASE}.f299.mp4.part-Frag11.part").write_bytes(b"half")
    (tmp_path / "Stackswopo youtube live 2026-10-08 19_00.txt").write_text(
        "YURRRR WTW 2026-10-08 19:00\nhttps://www.youtube.com/@x/live\n")

    found = L.find_live(str(tmp_path))
    assert len(found) == 1
    rec = found[0]
    assert len(rec.video) == len(rec.audio) == 10
    assert L.stream_title(rec) == "YURRRR WTW"
    # Kept a few fragments behind the newest: they may land out of order.
    assert L.edge_seconds(rec) == pytest.approx(10 - L.EDGE_FRAGS - 1, abs=0.2)
    assert L.fragments_for(rec, 2.5, 5.5)[0] <= 3
    assert L.fragments_for(rec, 2.5, 5.5)[-1] >= 6

    window = tmp_path / "w.mp4"
    started = L.cut_window(rec, 2.5, 5.5, str(window))
    assert started is not None and started <= 2.5
    kinds, seconds = _streams(window)
    assert kinds == ["audio", "video"]
    assert 3.0 <= seconds <= 6.0
    # The recording itself is untouched.
    assert len([n for n in os.listdir(tmp_path) if "-Frag" in n]) == 21
    # And a finished one (no new fragment for minutes) is left alone.
    assert L.find_live(str(tmp_path), now=time.time() + 600) == []

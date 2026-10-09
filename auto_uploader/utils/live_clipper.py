"""
utils/live_clipper.py - clips cut WHILE the stream is still live.

Everything else here waits for the stream to end: the recording is
merged, uploaded, transcribed whole, and only then clipped - so the first
clip of a four-hour stream posts four hours after its moment, and other
clippers get there first.

This reads the recording as it grows. With --live-from-start yt-dlp keeps
a live YouTube stream as one file per DASH fragment until the stream
ends: "<name>.f299.mp4.part-Frag<N>" (picture) and "<name>.f140.mp4.part-
Frag<N>" (sound), about a second each, every one a standalone MP4 that
carries its own place in the stream (measured on a live recording,
2026-10-08: Frag100 starts at 98.5 s). Every WINDOW_S of new stream, the
fragments for the newest stretch are joined (a byte copy - nothing is
re-encoded, nothing written into the recording) and run through the SAME
clip machinery a finished VOD gets: transcription, the model's picks,
vertical render, captions. The clips go to the watch folder like any
other, so Rumble and the social queue take them from there.

The recording itself is never touched: read-only, a few seconds at a
time, and nothing is done to a file that has stopped growing.

Ledger (logs/live_clips.json), per recording: how far it has been read,
and the stretches of stream already clipped - so a moment on the edge of
two windows is not clipped twice, and the full-VOD pass after the stream
leaves those moments alone (see clipped_ranges_for).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

# A window of new stream is clipped once there is this much of it.
WINDOW_S = 12 * 60
# Each window re-reads the end of the last, so a moment that straddles
# the boundary is still whole in one of them.
OVERLAP_S = 45
# The opening minutes are the waiting screen and "can you hear me".
SKIP_OPENING_S = 120
# A recording with no new fragment for this long is not live any more.
ACTIVE_S = 180
# Clips asked for per window. The model may return fewer - an empty
# window is an honest answer.
CLIPS_PER_WINDOW = 2
# Two clips overlapping by more than this share are the same moment.
SAME_MOMENT = 0.3

_FRAG = re.compile(r"^(?P<base>.+)\.f(?P<fmt>\d+)\.(?:mp4|m4a|webm)\.part"
                   r"-Frag(?P<n>\d+)$", re.IGNORECASE)
# The newest fragments may still be arriving out of order: stay this many
# behind the highest one.
EDGE_FRAGS = 3
# (base, format) -> "video" | "audio", decided once per recording.
_KINDS: dict = {}
_LOCK = threading.Lock()


@dataclass
class LiveRecording:
    base: str                    # "<name>.part01.ts"
    folder: str
    video: dict = field(default_factory=dict)   # fragment number -> path
    audio: dict = field(default_factory=dict)
    newest: float = 0.0          # mtime of the newest fragment

    @property
    def key(self) -> str:
        """The recording's own name, without the part/extension."""
        return re.sub(r"\.part\d+\.ts$", "", self.base, flags=re.I)


def _probe(path: str) -> Tuple[float, str]:
    """(duration seconds, "video"|"audio"|"") of a media file, or (0, "")."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type", "-of", "json", path],
            capture_output=True, text=True, timeout=60).stdout
        data = json.loads(out or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0, ""
    kinds = {s.get("codec_type") for s in data.get("streams") or []}
    kind = "video" if "video" in kinds else "audio" if "audio" in kinds else ""
    try:
        seconds = float((data.get("format") or {}).get("duration") or 0)
    except ValueError:
        seconds = 0.0
    return seconds, kind


def _start(path: str) -> Optional[float]:
    """Where a fragment sits in the stream (its start time), or None."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=start_time",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def find_live(folder: str, now: Optional[float] = None) -> List[LiveRecording]:
    """Recordings being written right now, picture and sound paired."""
    now = time.time() if now is None else now
    pieces: dict = {}            # (base, fmt) -> {n: path}
    newest: dict = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        match = _FRAG.match(name)
        if not match:
            continue
        path = os.path.join(folder, name)
        key = (match.group("base"), match.group("fmt"))
        pieces.setdefault(key, {})[int(match.group("n"))] = path
        try:
            newest[key[0]] = max(newest.get(key[0], 0.0),
                                 os.path.getmtime(path))
        except OSError:
            pass
    found: dict = {}
    for (base, fmt), frags in pieces.items():
        rec = found.setdefault(base, LiveRecording(base, folder))
        rec.newest = newest.get(base, 0.0)
        # Which is which by what is IN the files, not the format number -
        # 140 is audio today, and format ids are YouTube's to change.
        kind = _KINDS.get((base, fmt))
        if kind is None:
            kind = _probe(frags[min(frags)])[1]
            if kind:
                _KINDS[(base, fmt)] = kind
        if kind == "video" and not rec.video:
            rec.video = frags
        elif kind == "audio" and not rec.audio:
            rec.audio = frags
    return [rec for rec in found.values()
            if rec.video and now - rec.newest <= ACTIVE_S]


# Uploads wait while the recording's picture is this far behind its sound,
# and start again once it has caught up to within LAG_RESUME_S.
#
# 10/8: the 1080p60 picture fell 18 minutes behind while clips were being
# uploaded from the same machine during the stream; when the stream ended
# its fragments were refused and those 18 minutes have no picture. Sound
# and picture fragments share one numbering (one per second), so the gap
# between the newest of each is the lag, in seconds.
LAG_HOLD_S = 90
LAG_RESUME_S = 30
_HOLDING = {"on": False}


def recording_lag(cfg) -> float:
    """Seconds the live recording's picture is behind its sound; 0 when
    nothing is recording."""
    try:
        recordings = find_live(recording_folder(cfg))
    except Exception:
        return 0.0
    lag = 0.0
    for rec in recordings:
        if rec.video and rec.audio:
            lag = max(lag, float(max(rec.audio) - max(rec.video)))
    return lag


def uploads_on_hold(cfg) -> bool:
    """True while uploads should wait for the recording to catch up."""
    lag = recording_lag(cfg)
    if _HOLDING["on"] and lag <= LAG_RESUME_S:
        _HOLDING["on"] = False
        print(f"[Live] The recording has caught up ({lag:.0f}s behind) - "
              f"uploads and posts resume.")
    elif not _HOLDING["on"] and lag >= LAG_HOLD_S:
        _HOLDING["on"] = True
        print(f"[Live] The recording's picture is {lag:.0f}s behind its "
              f"sound - holding uploads and posts so it can catch up.")
    return _HOLDING["on"]


def edge_seconds(rec: LiveRecording) -> float:
    """How far into the stream the recording safely reaches."""
    numbers = sorted(rec.video)
    if not numbers:
        return 0.0
    target = numbers[-1] - EDGE_FRAGS
    usable = [n for n in numbers if n <= target] or numbers[:1]
    started = _start(rec.video[usable[-1]])
    return float(started or 0.0)


def fragments_for(rec: LiveRecording, start: float, end: float) -> List[int]:
    """The fragment numbers covering [start, end] of the stream.

    Fragments are about a second each and say where they start, so two
    probes (the first fragment and the edge) give the spacing and the
    rest is arithmetic - not one probe per fragment."""
    numbers = sorted(rec.video)
    if not numbers:
        return []
    first = numbers[0]
    last = max(first, numbers[-1] - EDGE_FRAGS)
    t_first = _start(rec.video[first]) or 0.0
    t_last = _start(rec.video.get(last, rec.video[first])) or t_first
    step = (t_last - t_first) / (last - first) if last > first else 1.0
    step = step if step > 0 else 1.0
    lo = max(first, first + int((start - t_first) / step))
    hi = min(last, first + int((end - t_first) / step) + 1)
    return [n for n in numbers if lo <= n <= hi]


# ── the ledger ────────────────────────────────────────────────────────

def _ledger_path(logs_folder: str) -> str:
    return os.path.join(logs_folder or "logs", "live_clips.json")


def load_ledger(logs_folder: str) -> dict:
    try:
        with open(_ledger_path(logs_folder), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ledger(logs_folder: str, ledger: dict) -> None:
    path = _ledger_path(logs_folder)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(ledger, handle, indent=1, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def overlap_share(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """How much of the shorter of two stretches the other covers."""
    inter = min(a[1], b[1]) - max(a[0], b[0])
    if inter <= 0:
        return 0.0
    shorter = max(0.001, min(a[1] - a[0], b[1] - b[0]))
    return inter / shorter


def is_already_clipped(span: Tuple[float, float], ranges) -> bool:
    return any(overlap_share(span, tuple(r)) > SAME_MOMENT for r in ranges)


def stream_title(rec: LiveRecording) -> str:
    """The stream's title from the recorder's "<name>.txt", date removed."""
    path = os.path.join(rec.folder, rec.key + ".txt")
    try:
        with open(path, encoding="utf-8") as handle:
            first = handle.readline().strip()
    except OSError:
        return ""
    return re.sub(r"\s*\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}$", "", first).strip()


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def clipped_ranges_for(logs_folder: str, video_path: str, title: str = "",
                       now: Optional[float] = None,
                       duration: Optional[float] = None
                       ) -> List[Tuple[float, float]]:
    """Stretches of this stream the live pass already clipped, in the
    stream's own time - for the full-VOD pass to leave alone.

    Matched on the recording's name, then the stream title, then - since
    a delivered VOD is often renamed after its title - the most recent
    live-clipped stream that ended in the last 12 hours.
    """
    now = time.time() if now is None else now
    ledger = load_ledger(logs_folder)
    if not ledger:
        return []
    name = _normal(os.path.basename(video_path))
    wanted = _normal(title)
    for key, entry in ledger.items():
        if _normal(key) and _normal(key) in name:
            return [tuple(r) for r in entry.get("clipped", [])]
    for entry in ledger.values():
        said = _normal(entry.get("title", ""))
        if said and (said in name or (wanted and said == wanted)):
            return [tuple(r) for r in entry.get("clipped", [])]
    recent = [e for e in ledger.values()
              if now - float(e.get("updated", 0)) < 12 * 3600]
    if recent:
        newest = max(recent, key=lambda e: float(e.get("updated", 0)))
        read = float(newest.get("read_until", 0.0))
        length = duration if duration is not None else _probe(video_path)[0]
        # Only for the same stream: a VOD about as long as what was read
        # live (it runs on past the last window), never an old backfill.
        if read and length and read * 0.8 <= length <= read * 2.0:
            return [tuple(r) for r in newest.get("clipped", [])]
    return []


# ── one window ────────────────────────────────────────────────────────

def _read(path: str) -> bytes:
    with open(path, "rb") as src:
        return src.read()


# Fragments read at once. Each file costs ~20-60 ms just to OPEN on the
# USB drive (the antivirus looks at every new file), so a 24-minute
# window - 2,900 files - took 2.5 minutes read one by one (2026-10-08).
# In parallel the opens overlap; in batches, so memory stays ~100 MB.
READ_THREADS = 8
READ_BATCH = 64


def _join(paths: List[str], out_path: str) -> bool:
    from concurrent.futures import ThreadPoolExecutor

    try:
        with open(out_path, "wb") as dst, \
                ThreadPoolExecutor(READ_THREADS) as pool:
            for at in range(0, len(paths), READ_BATCH):
                for chunk in pool.map(_read, paths[at:at + READ_BATCH]):
                    dst.write(chunk)
        return os.path.getsize(out_path) > 0
    except OSError:
        return False


def cut_window(rec: LiveRecording, start: float, end: float,
               out_path: str) -> Optional[float]:
    """Join the fragments for [start, end] into one playable file.

    Returns where the window really starts in the stream (fragments do
    not land exactly on `start`), or None if it could not be made. The
    recording is only ever read."""
    numbers = fragments_for(rec, start, end)
    if not numbers:
        return None
    first_start = _start(rec.video[numbers[0]])
    if first_start is None:
        return None
    video_tmp = out_path + ".v.mp4"
    audio_tmp = out_path + ".a.mp4"
    try:
        if not _join([rec.video[n] for n in numbers], video_tmp):
            return None
        heard = [rec.audio[n] for n in numbers if n in rec.audio]
        args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", video_tmp]
        if heard and _join(heard, audio_tmp):
            args += ["-i", audio_tmp, "-map", "0:v:0", "-map", "1:a:0"]
        else:
            args += ["-map", "0:v:0", "-map", "0:a:0?"]
        args += ["-c", "copy", "-movflags", "+faststart", out_path]
        try:
            done = subprocess.run(args, capture_output=True, text=True,
                                  timeout=300)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0 or not os.path.isfile(out_path) \
                or os.path.getsize(out_path) == 0:
            return None
        return first_start
    finally:
        for temp in (video_tmp, audio_tmp):
            try:
                os.remove(temp)
            except OSError:
                pass


def _clip_config(cfg, first_window: bool):
    """cfg for a window: what makes sense for a stretch of a live stream."""
    import copy

    window_cfg = copy.copy(cfg)
    clips = dict(getattr(cfg, "clips", {}) or {})
    clips.update({
        # Only the first window has the waiting screen in it.
        "skip_intro_seconds": SKIP_OPENING_S if first_window else 0,
        # A window ends mid-stream, not on the goodbyes.
        "skip_outro_seconds": 0,
        # Uploads a copy and waits on a remote index - too slow live.
        "use_twelvelabs": False,
        # No replay curve or chat log exists for a stream still going.
        "use_replay_heat": False,
        "use_chat": False,
    })
    window_cfg.clips = clips
    return window_cfg


def _remove_clip(path: str) -> None:
    stem = os.path.splitext(path)[0]
    for candidate in (path, stem + "_caption.txt", stem + "_line.txt",
                      stem + "_subject.txt", stem + ".txt"):
        try:
            os.remove(candidate)
        except OSError:
            pass


def clip_window(cfg, rec: LiveRecording, entry: dict, start: float,
                end: float, deliver: Callable, work_folder: str,
                make_clips: Optional[Callable] = None) -> int:
    """Clip one window; deliver the clips not already clipped. How many."""
    if make_clips is None:
        from utils.clip_runner import make_clips

    index = int(entry.get("windows", 0)) + 1
    title = entry.get("title") or rec.key
    # Named after the recording, not the title: the date in the name is
    # how a delivered clip's "stream_date" is read, and the name is how
    # the VOD pass matches this ledger.
    safe = re.sub(r'[\\/:*?"<>|]+', "", rec.key).strip() or "Stream"
    os.makedirs(work_folder, exist_ok=True)
    window = os.path.join(work_folder, f"{safe} LIVE {index:02d}.mp4")
    real_start = cut_window(rec, start, end, window)
    if real_start is None:
        print(f"[Live] Could not read {start / 60:.0f}-{end / 60:.0f} min of "
              f"the recording - trying again next pass.")
        return -1
    start = real_start
    print(f"[Live] Clipping minutes {start / 60:.0f}-{end / 60:.0f} of "
          f"\"{title}\" while it is still live...")
    from utils.keep_awake import KeepAwake

    try:
        with KeepAwake("clipping a live stream"):
            # A catch-up window twice as long gets twice the clips.
            count = max(CLIPS_PER_WINDOW,
                        round(CLIPS_PER_WINDOW * (end - start) / WINDOW_S))
            run = make_clips(_clip_config(cfg, first_window=index == 1),
                             window, title, count=count,
                             notify=False, transcribe_if_needed=True)
    except Exception as exc:
        # Moved past, not retried: a window that crashes the clip run
        # once will crash it every minute. The VOD pass after the
        # stream still reads this stretch.
        print(f"[Live] Clipping that stretch failed ({exc}) - moving on; "
              f"the full stream is clipped again when it ends.")
        return 0
    finally:
        try:
            os.remove(window)
        except OSError:
            pass
    if getattr(run, "skipped_reason", ""):
        print(f"[Live] Nothing cut from that stretch: {run.skipped_reason}")
        return 0
    clipped = [tuple(r) for r in entry.get("clipped", [])]
    keep = []
    for clip in getattr(run, "clips", []) or []:
        span = (start + clip.spec.start, start + clip.spec.end)
        if is_already_clipped(span, clipped):
            _remove_clip(clip.path)
            continue
        clipped.append(span)
        keep.append(clip)
    run.clips = keep
    delivered = deliver(run) if keep else 0
    entry["clipped"] = [list(r) for r in clipped]
    print(f"[Live] {delivered} clip(s) from \"{title}\" on their way out "
          f"- the stream is still live.")
    return delivered


# ── the pass ──────────────────────────────────────────────────────────

# A recording joined late (the uploader restarted mid-stream, or the
# recorder caught up from the start) is read this much at a time, so the
# first clips come quickly instead of after one huge window.
MAX_WINDOW_S = 2 * WINDOW_S
# A window that cannot be read this many passes in a row is skipped.
MAX_MISSES = 3
_MISSES: dict = {}


def recording_folder(cfg) -> str:
    general = getattr(cfg, "general", None)
    folder = str(getattr(general, "recording_folder", "") or "")
    if not folder:
        folder = os.path.join(getattr(cfg, "project_root", "."), "recording")
    return folder


def work_folder(cfg) -> str:
    """Where windows are built: the computer's own drive by default.

    A window is ~900 MB joined, muxed and read back by the clip run. On
    the USB drive the recording is on, that was a minute of the window's
    wait, competing with the recorder writing the stream."""
    clips = getattr(cfg, "clips", {}) or {}
    return str(clips.get("live_work_folder") or os.path.join(
        tempfile.gettempdir(), "autobleep_live_windows"))


def _sweep(folder: str, older_than_s: float = 3 * 3600) -> None:
    """Remove windows a crashed pass left behind - ~1 GB each."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    cutoff = time.time() - older_than_s
    for name in names:
        path = os.path.join(folder, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def tick(cfg, deliver: Callable, folder: str = "",
         now: Optional[float] = None,
         make_clips: Optional[Callable] = None) -> int:
    """One pass over the live recordings: clip a window where one is due.
    Returns how many clips were delivered."""
    folder = folder or recording_folder(cfg)
    logs = str(getattr(getattr(cfg, "general", None), "logs_folder", "")
               or "logs")
    work = work_folder(cfg)
    _sweep(work)
    total = 0
    for rec in find_live(folder, now):
        seconds = edge_seconds(rec)
        ledger = load_ledger(logs)
        entry = ledger.get(rec.base) or {
            "title": "", "read_until": 0.0, "windows": 0, "clipped": [],
            "started": time.time()}
        entry["title"] = entry.get("title") or stream_title(rec)
        ready = seconds
        read_until = float(entry.get("read_until", 0.0))
        if ready - read_until < WINDOW_S:
            continue
        start = max(0.0, read_until - OVERLAP_S) if read_until > 0 else 0.0
        end = min(ready, start + MAX_WINDOW_S)
        delivered = clip_window(cfg, rec, entry, start, end, deliver, work,
                                make_clips=make_clips)
        if delivered < 0:
            # Could not read it: try again next pass - but not forever,
            # each try joins a few hundred MB.
            miss = (rec.base, round(start))
            _MISSES[miss] = _MISSES.get(miss, 0) + 1
            if _MISSES[miss] < MAX_MISSES:
                continue
            print(f"[Live] Skipping minutes {start / 60:.0f}-{end / 60:.0f} "
                  f"after {MAX_MISSES} failed reads.")
            delivered = 0
        entry["read_until"] = end
        entry["windows"] = int(entry.get("windows", 0)) + 1
        entry["updated"] = time.time()
        ledger = load_ledger(logs)
        ledger[rec.base] = entry
        save_ledger(logs, ledger)
        total += delivered
    return total


_WORKER: dict = {"thread": None}


def start_background(cfg, deliver: Callable) -> bool:
    """Run tick() on a worker thread unless one is already going.

    A window takes minutes (transcription, the model, the render); the
    watcher's own loop - posting the queue among other things - must not
    wait for it. True if a pass was started."""
    worker = _WORKER.get("thread")
    if worker is not None and worker.is_alive():
        return False

    def run():
        if not _LOCK.acquire(blocking=False):
            return
        try:
            tick(cfg, deliver)
        except Exception as exc:          # never take the watcher down
            print(f"[Live] Live clipping pass failed: {exc}")
        finally:
            _LOCK.release()

    worker = threading.Thread(target=run, daemon=True, name="live-clipper")
    _WORKER["thread"] = worker
    worker.start()
    return True


def wait_idle(timeout: float = 900.0) -> bool:
    """Wait for a live pass in progress to finish - so the VOD pass reads
    a ledger that has the last window's clips in it. True if idle."""
    worker = _WORKER.get("thread")
    if worker is not None and worker.is_alive():
        print("[Live] Waiting for the last live window to finish...")
        worker.join(timeout)
        return not worker.is_alive()
    return True

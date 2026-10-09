"""
Where somebody LAUGHED, second by second.

WHY
---
Every other signal here reads the words or measures how loud it got.
Neither hears a laugh: Whisper drops most laughter from the transcript,
and a laugh is not especially loud - a car crash is louder. On a channel
whose clips live or die on "is it funny", the streamer cracking up, or
the other players losing it on voice, is the strongest single sign that
a moment landed. chat_report.py said it outright: "no laughter detector
exists yet". This is it.

HOW
---
The AudioSet tagger already on this machine for the music guard (PANNs
Cnn14, ~/panns_data) hears 527 kinds of sound, laughter among them. It
listens to two seconds at a time, one second apart, and the laughter
score for each second is kept - nothing else, no audio is stored. On the
GPU a three-hour stream takes about a minute.

The uploader's own Python can be one without the GPU libraries (this
machine runs it on 3.14 with a CPU-only torch, and has a 3.12 with CUDA
torch and PANNs). Then the job goes to that one, the same way the music
guard does it.

WHAT IT IS AND IS NOT
---------------------
A signal, not a verdict. A laugh can be at nothing, and a clip can be
funny with nobody laughing. So, like loudness, it only lifts windows the
words already put forward (laugh_bonus), and the model choosing clips is
told "laughter heard" as a hint. Any failure returns [] - "no opinion" -
and clipping goes on exactly as before.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import List, Optional, Sequence, Tuple

LAUGH_LABELS = ("Laughter", "Giggle", "Snicker", "Belly laugh",
                "Chuckle, chortle", "Baby laughter")
SAMPLE_RATE = 32000
WIN_S = 2.0
HOP_S = 1.0
BATCH = 64
# Below this, a reading is the tagger hedging, not a laugh.
FLOOR = 0.15
# A window whose loudest laugh reaches this is told to the model.
# Measured on a 2h16m stream (2026-10-08): half its seconds read under
# 0.002, 99% under 0.07; 15 seconds reached 0.2 - the dance bit at 46:00
# read 0.73. Laughs under game audio score low; this keeps the hint rare.
SHOWN = 0.20
_TIMEOUT = 60 * 30
_CHILD_FLAG = "AUTOBLEEP_LAUGHTER_CHILD"
_MODEL: dict = {}
_HELPER: dict = {}


# ── listening ─────────────────────────────────────────────────────────

def _load():
    """(tagger, device, label indices), loaded once per process."""
    if _MODEL:
        return _MODEL["tagger"], _MODEL["device"], _MODEL["labels"]
    import torch
    from panns_inference.models import Cnn14

    from .music_guard import _device, _label_index, ensure_models

    ckpt = ensure_models(lambda *_: None)
    device = _device()
    tagger = Cnn14(sample_rate=SAMPLE_RATE, window_size=1024, hop_size=320,
                   mel_bins=64, fmin=50, fmax=14000, classes_num=527)
    state = torch.load(ckpt, map_location="cpu", weights_only=False)
    tagger.load_state_dict(state["model"])
    tagger.to(device).eval()
    index = _label_index()
    labels = [index[name] for name in LAUGH_LABELS if name in index]
    _MODEL.update(tagger=tagger, device=device, labels=labels)
    return tagger, device, labels


def _tag(frames, tagger, device, labels) -> List[float]:
    import numpy as np
    import torch

    out: List[float] = []
    for at in range(0, len(frames), BATCH):
        batch = np.ascontiguousarray(frames[at:at + BATCH], dtype=np.float32)
        with torch.no_grad():
            scores = tagger(torch.from_numpy(batch).to(device),
                            None)["clipwise_output"]
        out.extend(scores[:, labels].max(1).values.float().cpu().tolist())
    return out


def scan_here(source: str) -> List[float]:
    """Laughter score per second of `source`, in this Python."""
    import numpy as np

    tagger, device, labels = _load()
    win, hop = int(SAMPLE_RATE * WIN_S), int(SAMPLE_RATE * HOP_S)
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", source, "-map", "0:a:0",
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    curve: List[float] = []
    pending = np.zeros(0, dtype=np.float32)
    try:
        while True:
            raw = proc.stdout.read(SAMPLE_RATE * 2 * 120)    # 2 min
            if raw:
                pending = np.concatenate([pending, np.frombuffer(
                    raw, np.int16).astype(np.float32) / 32768.0])
            elif len(pending) > hop // 2:
                # The last second or two: padded, still heard.
                pending = np.pad(pending, (0, max(0, win - len(pending))))
            if len(pending) >= win:
                count = (len(pending) - win) // hop + 1
                frames = np.lib.stride_tricks.sliding_window_view(
                    pending, win)[::hop][:count]
                curve.extend(_tag(frames, tagger, device, labels))
                pending = pending[count * hop:]
            if not raw:
                break
    finally:
        proc.stdout.close()
        proc.wait()
    return [round(value, 4) for value in curve]


# ── which Python does it ──────────────────────────────────────────────

_PROBE = ("import importlib.util as u, torch;"
          "print('READY' if u.find_spec('panns_inference') else 'NO',"
          " 'CUDA' if torch.cuda.is_available() else 'CPU')")


def _ready_here() -> bool:
    import importlib.util

    return all(importlib.util.find_spec(m) is not None
               for m in ("panns_inference", "torch", "numpy"))


def _helper_python() -> str:
    """A Python here with PANNs - one with a GPU first. "" if none."""
    if "path" in _HELPER:
        return _HELPER["path"]
    found = [os.environ.get("LAUGHTER_PYTHON", ""),
             os.environ.get("MUSIC_GUARD_PYTHON", "")]
    try:
        listing = subprocess.run(["py", "-0p"], capture_output=True,
                                 text=True, timeout=20).stdout
        found += [line.split()[-1] for line in listing.splitlines()
                  if line.strip().lower().endswith("python.exe")]
    except (OSError, subprocess.SubprocessError):
        pass
    me = os.path.normcase(sys.executable)
    best, fallback = "", ""
    for path in dict.fromkeys(p for p in found if p):
        if os.path.normcase(path) == me or not os.path.isfile(path):
            continue
        try:
            answer = subprocess.run([path, "-c", _PROBE], capture_output=True,
                                    text=True, timeout=120).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if "READY CUDA" in answer:
            best = path
            break
        if "READY" in answer and not fallback:
            fallback = path
    _HELPER["path"] = best or fallback
    return _HELPER["path"]


def _scan_in(python: str, source: str) -> List[float]:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fd, out = tempfile.mkstemp(prefix="laughter_", suffix=".json")
    os.close(fd)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env[_CHILD_FLAG] = "1"
    try:
        done = subprocess.run([python, "-m", "autoreel.laughter", source, out],
                              cwd=root, env=env, capture_output=True,
                              text=True, timeout=_TIMEOUT)
        if done.returncode != 0:
            tail = (done.stderr or done.stdout or "").strip().splitlines()
            raise RuntimeError(tail[-1] if tail else f"exit {done.returncode}")
        with open(out, encoding="utf-8") as handle:
            return [float(v) for v in json.load(handle)]
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def listen(source: str, say=print) -> List[float]:
    """Laughter score (0..1) per second of `source`; [] on any failure.

    Called as `measure` (below) - the name the test suite replaces, so no
    test loads the model by accident."""
    try:
        if _ready_here():
            return scan_here(source)
        if os.environ.get(_CHILD_FLAG):
            return []
        helper = _helper_python()
        if not helper:
            say("[Clips] No laughter detector here (needs panns_inference) "
                "- going on the words and loudness.")
            return []
        return _scan_in(helper, source)
    except Exception as exc:              # a hint must never cost a clip
        say(f"[Clips] Could not listen for laughter ({exc}) - going on "
            f"without it.")
        return []


measure = listen


# ── reading the curve ─────────────────────────────────────────────────

def peak(curve: Sequence[float], start: float,
         end: float) -> Tuple[float, float]:
    """(loudest laugh 0..1, seconds into the window it starts) in
    [start, end]. A reading covers two seconds, so the one starting a
    second before the window still counts."""
    if not curve or end <= start:
        return 0.0, 0.0
    first = max(0, int(start // HOP_S) - 1)
    last = min(len(curve), int(end // HOP_S) + 1)
    best, at = 0.0, first
    for index in range(first, last):
        if curve[index] > best:
            best, at = curve[index], index
    return best, max(0.0, at * HOP_S - start)


def laugh_bonus(curve: Sequence[float], start: float, end: float,
                per: float = 0.8, cap: float = 0.4) -> float:
    """A multiplier for a window's score, 1.0 when there is no signal.

    Wider cap than loudness (0.35): a laugh is evidence the moment was
    funny, where loud is only evidence something happened. Still capped:
    it lifts a window the words liked, it never carries one alone."""
    best, _ = peak(curve, start, end)
    if best <= FLOOR:
        return 1.0
    return 1.0 + min(cap, (best - FLOOR) * per)


def _main(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m autoreel.laughter <video> <out.json>")
        return 2
    curve = scan_here(args[0])
    with open(args[1], "w", encoding="utf-8") as handle:
        json.dump(curve, handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

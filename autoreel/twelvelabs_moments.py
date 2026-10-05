"""Where a video model, watching the stream itself, says the clips are.

Every other signal here is about the stream without looking at it: the
transcript, chat speed, how loud the room got, what people rewatched.
Twelve Labs' Pegasus model watches the picture and the sound together,
which is what catches a moment the words miss - a chase, a fall, a face.

Its picks do not choose the clips. They become one more per-second curve,
like chat and the most-replayed graph, that lifts the score of any window
it overlaps (capped, see seen_bonus). The transcript and the language
model still decide - a video model that likes a car crash cannot tell
whether the joke after it landed.

The limits shape everything below:

- a local upload is at most 200 MB, so the stream is cut into a small
  640x360 copy, in pieces;
- one analysis covers at most an hour, so each piece is under an hour;
- the free plan is 600 minutes in TOTAL - about three 3-hour streams - so
  every minute sent is counted in logs/twelvelabs_usage.json and nothing
  is sent once the allowance is spent.

Every uploaded piece is deleted from Twelve Labs as soon as it has been
read, and the local copies are deleted too.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Callable, List, Optional, Tuple

KEY_NAME = "TWELVELABS_API_KEY"

# Under Pegasus' one-hour limit, with room for a rounding error.
CHUNK_SECONDS = 55 * 60
# Under the 200 MB local-upload limit.
MAX_UPLOAD_BYTES = 190 * 1024 * 1024
# The free plan's whole allowance.
DEFAULT_MINUTES = 600
READY_TIMEOUT_S = 30 * 60
POLL_S = 10

PROMPT = """\
This is part of a live stream by the streamer Stackswopo - mostly GTA
roleplay, sometimes reacting or talking to people on camera.

Find up to 8 moments that would make the best 15 to 60 second short clips:
funny lines and comebacks, big reactions, arguments, arrests and chases,
absurd or chaotic situations. Skip intros, loading screens, menus, AFK
stretches and quiet driving.

For each moment give start and end in seconds from the start of THIS
video, a score from 0 to 1 for how clip-worthy it is, and a few words on
why.\
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "score": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["start", "end", "score"],
            },
        },
    },
    "required": ["moments"],
}

Moment = Tuple[float, float, float, str]          # start, end, score, why


def api_key() -> str:
    return os.environ.get(KEY_NAME, "").strip()


# ── the allowance ────────────────────────────────────────────────────────────

def _usage_path(logs_folder: str) -> str:
    return os.path.join(logs_folder or "logs", "twelvelabs_usage.json")


def minutes_used(logs_folder: str) -> float:
    try:
        with open(_usage_path(logs_folder), encoding="utf-8") as handle:
            return float(json.load(handle).get("minutes", 0.0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def _add_minutes(logs_folder: str, minutes: float) -> None:
    path = _usage_path(logs_folder)
    total = minutes_used(logs_folder) + minutes
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"minutes": round(total, 1)}, handle)
    except OSError:
        pass


# ── the small copy ───────────────────────────────────────────────────────────

def pieces(duration: float, chunk: float = CHUNK_SECONDS) -> List[tuple]:
    """(start, length) for each piece of a `duration`-second stream."""
    out, start = [], 0.0
    while start < duration - 1:
        length = min(chunk, duration - start)
        out.append((start, length))
        start += length
    return out


def _proxy(source: str, start: float, length: float, out_path: str,
           video_kbps: int = 250) -> bool:
    """A 640x360 copy of one piece, small enough to upload."""
    from autoreel.gpu import best_h264_encoder
    enc = best_h264_encoder()
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", source,
            "-vf", "scale=-2:360,fps=15",
            "-c:v", enc, "-preset", ("p4" if enc == "h264_nvenc" else "veryfast"),
            "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k",
            "-bufsize", f"{video_kbps * 2}k",
            "-c:a", "aac", "-b:a", "48k", "-ac", "1", out_path]
    try:
        subprocess.run(args, timeout=60 * 60, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 0


# ── asking ───────────────────────────────────────────────────────────────────

def read_moments(raw: str, offset: float, length: float) -> List[Moment]:
    """The model's answer as stream-time moments; junk is dropped."""
    try:
        data = json.loads(raw or "")
    except ValueError:
        return []
    found = []
    for item in (data.get("moments") if isinstance(data, dict) else None) or ():
        try:
            start, end = float(item["start"]), float(item["end"])
            score = max(0.0, min(1.0, float(item.get("score", 0.5))))
        except (KeyError, TypeError, ValueError):
            continue
        start, end = max(0.0, start), min(length, end)
        if end - start < 3:
            continue
        found.append((offset + start, offset + end, score,
                      str(item.get("why", ""))[:120]))
    return found


def _ask_piece(client, path: str, say: Callable) -> str:
    """Upload one piece, analyse it, delete it. The raw JSON answer."""
    from twelvelabs.types import SyncResponseFormat
    from twelvelabs.types.video_context import VideoContext_AssetId

    with open(path, "rb") as handle:
        asset = client.assets.create(method="direct", file=handle)
    try:
        waited = 0
        while True:
            status = client.assets.retrieve(asset.id).status
            if status == "ready":
                break
            if status == "failed":
                raise RuntimeError("Twelve Labs could not read the piece")
            if waited >= READY_TIMEOUT_S:
                raise RuntimeError("Twelve Labs took too long to process it")
            time.sleep(POLL_S)
            waited += POLL_S
        response = client.analyze(
            video=VideoContext_AssetId(asset_id=asset.id),
            prompt=PROMPT,
            response_format=SyncResponseFormat(type="json_schema",
                                               json_schema=SCHEMA),
            max_tokens=2048)
        return response.data or ""
    finally:
        try:
            client.assets.delete(asset.id)
        except Exception as exc:
            say(f"[Clips] Could not delete the upload from Twelve Labs "
                f"({exc}) - remove it on their dashboard.")


def find_moments(source: str, duration: float, logs_folder: str = "logs",
                 allowance: float = DEFAULT_MINUTES, client=None,
                 say: Callable = print) -> List[Moment]:
    """Twelve Labs' picks across the whole stream, or [] for any reason.

    Never raises: this is one signal among several, and a stream with no
    opinion from it is clipped exactly as it was before it existed.
    """
    if not source or duration <= 0 or not os.path.isfile(source):
        return []
    if client is None:
        key = api_key()
        if not key:
            return []
        try:
            from twelvelabs import TwelveLabs
        except ImportError:
            say("[Clips] TWELVELABS_API_KEY is set but the twelvelabs "
                "package is not installed - run: pip install twelvelabs")
            return []
        client = TwelveLabs(api_key=key)
    if not shutil.which("ffmpeg"):
        return []

    left = allowance - minutes_used(logs_folder)
    plan = pieces(duration)
    needed = sum(length for _s, length in plan) / 60.0
    if left < needed:
        say(f"[Clips] Twelve Labs: {left:.0f} of {allowance:.0f} minutes "
            f"left, this stream needs {needed:.0f} - skipping it. Raise "
            f"clips.twelvelabs_minutes once the plan allows more.")
        return []

    say(f"[Clips] Twelve Labs is watching the stream ({len(plan)} piece(s), "
        f"{needed:.0f} min of {left:.0f} left)...")
    workspace = tempfile.mkdtemp(prefix="twelvelabs_")
    moments: List[Moment] = []
    try:
        for number, (start, length) in enumerate(plan, 1):
            piece = os.path.join(workspace, f"piece_{number}.mp4")
            if not _proxy(source, start, length, piece):
                say(f"[Clips] Twelve Labs: could not make piece {number}.")
                continue
            if os.path.getsize(piece) > MAX_UPLOAD_BYTES:
                _proxy(source, start, length, piece, video_kbps=150)
            if os.path.getsize(piece) > MAX_UPLOAD_BYTES:
                say(f"[Clips] Twelve Labs: piece {number} is still over "
                    f"200 MB - skipped.")
                continue
            try:
                raw = _ask_piece(client, piece, say)
            except Exception as exc:
                say(f"[Clips] Twelve Labs: piece {number} failed ({exc}).")
                continue
            finally:
                try:
                    os.remove(piece)
                except OSError:
                    pass
            _add_minutes(logs_folder, length / 60.0)
            found = read_moments(raw, start, length)
            moments.extend(found)
            say(f"[Clips] Twelve Labs: piece {number}/{len(plan)} - "
                f"{len(found)} moment(s).")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return sorted(moments)


# ── as a signal ──────────────────────────────────────────────────────────────

def seen_curve(moments: List[Moment], duration: float) -> list:
    """One value per second: the best score of any moment covering it."""
    if not moments or duration <= 0:
        return []
    out = [0.0] * (int(duration) + 1)
    for start, end, score, _why in moments:
        for second in range(max(0, int(start)), min(len(out), int(end) + 1)):
            out[second] = max(out[second], score)
    return out


def seen_bonus(values: list, start: float, end: float,
               cap: float = 0.5) -> float:
    """A multiplier for a window's score, 1.0 with no opinion.

    Capped like chat and replays: it lifts what it saw, it does not
    decide - the transcript still has to make the case.
    """
    if not values:
        return 1.0
    first, last = max(0, int(start)), min(len(values), int(end) + 1)
    if last <= first:
        return 1.0
    return 1.0 + cap * max(values[first:last])

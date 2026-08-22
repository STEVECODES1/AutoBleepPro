"""End-to-end check: does the pipeline actually do what it claims, on a
real video file?

Everything else in tests/ runs against stand-ins. This runs ffmpeg for
real, on footage built for the purpose, and measures the output.

The footage is a clock: source second N is a unique colour AND a pure
tone at 300 + N*50 Hz. So a rendered clip can be asked two independent
questions - which second does this picture come from, and which second
does this sound come from - and disagreement between them is exactly the
"the audio doesn't match the clip" complaint, made into a number.

What it proves, in order:

  1. CENSOR      a flagged word is silenced, its neighbours are not, and
                 the video is neither cut nor re-encoded
  2. FRAMING     every crop strategy gives 1080x1920 at the right length
  3. CUT         video and audio both start on the second that was asked
                 for
  4. CAPTIONS    the caption track is rebased to clip time and paints
                 only while someone is speaking
  5. CAPTION     a word the audio muted is not printed underneath it
     CENSORING

Whisper is not needed and not used: the transcript is seeded into the
same cache the real run reads, so this takes about a minute instead of
an hour.

    python tools/e2e_check.py

Exit code 0 means every check passed.
"""

from __future__ import annotations

import colorsys
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# auto_uploader's modules import each other as `utils.x`, so its own
# directory has to be importable as well as the repo root.
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

SECONDS = 20
WIDTH, HEIGHT = 1280, 720
FLAG_WORD = "murder"      # in compliance's default "violence" list
FLAG_AT = 8               # source second it is spoken at
PAD_MS = 250              # censor_video's default padding

_FAILURES: list[str] = []
_CHECKS = 0


def check(ok: bool, detail: str) -> bool:
    global _CHECKS
    _CHECKS += 1
    if not ok:
        _FAILURES.append(detail)
    return ok


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def colour_for(second: int) -> tuple[float, float, float]:
    r, g, b = colorsys.hsv_to_rgb(second / SECONDS, 0.9, 0.9)
    return r * 255, g * 255, b * 255


def build_clock(dest: str) -> str:
    """One second per colour, one second per tone, concatenated."""
    parts = []
    for i in range(SECONDS):
        r, g, b = colour_for(i)
        hexed = "#%02x%02x%02x" % (int(r), int(g), int(b))
        out = os.path.join(dest, f"_p{i:02d}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", f"color=c={hexed}:s={WIDTH}x{HEIGHT}:r=30:d=1",
             "-f", "lavfi", "-i", f"sine=frequency={300 + i * 50}:duration=1",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", out], check=True)
        parts.append(out)

    listing = os.path.join(dest, "_parts.txt")
    with open(listing, "w", encoding="utf-8") as fh:
        for part in parts:
            fh.write(f"file '{part}'\n")
    clock = os.path.join(dest, "clock.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", listing, "-c", "copy", clock], check=True)
    for part in parts + [listing]:
        os.remove(part)
    return clock


# ── measurement ──────────────────────────────────────────────────────────

def mean_db(path: str, start: float, end: float) -> float:
    out = _run(["ffmpeg", "-v", "info", "-ss", str(start), "-to", str(end),
                "-i", path, "-af", "volumedetect", "-f", "null", "-"]).stderr
    found = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", out)
    if not found:
        return float("nan")
    return float("-inf") if found.group(1) == "-inf" else float(found.group(1))


def duration(path: str) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", path]).stdout.strip()
    return float(out) if out else 0.0


def dimensions(path: str) -> str:
    return _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0",
                 path]).stdout.strip()


def second_from_picture(path: str, at: float):
    """Which source second this frame came from, read off its colour."""
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at), "-i", path, "-frames:v", "1",
         "-vf", "crop=iw/3:ih/3:iw/3:ih/3,scale=1:1", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    if len(raw) < 3:
        return None
    px = np.frombuffer(raw[:3], dtype=np.uint8).astype(float)
    dists = [np.linalg.norm(px - np.array(colour_for(i))) for i in range(SECONDS)]
    return int(np.argmin(dists))


def second_from_sound(path: str, at: float, window: float = 0.4):
    """The same question asked of the audio, so the two can disagree."""
    import numpy as np

    probe = path + ".probe.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(at), "-t",
                    str(window), "-i", path, "-ac", "1", "-ar", "16000",
                    probe], check=True)
    try:
        with wave.open(probe) as fh:
            data = np.frombuffer(fh.readframes(fh.getnframes()), dtype=np.int16)
            rate = fh.getframerate()
    finally:
        os.remove(probe)
    if data.size < 256 or np.abs(data).max() < 50:
        return None
    spectrum = np.abs(np.fft.rfft(data * np.hanning(data.size)))
    peak = np.fft.rfftfreq(data.size, 1 / rate)[int(np.argmax(spectrum))]
    return int(round((peak - 300) / 50))


def frame_gray(path: str, at: float):
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at), "-i", path, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).astype(float)


def words_every_second(flagged_at: int = FLAG_AT) -> list[dict]:
    return [{"word": f" {FLAG_WORD}" if i == flagged_at else f" word{i}",
             "start": float(i), "end": float(i) + 0.5, "probability": 0.95}
            for i in range(SECONDS)]


# ── the checks ───────────────────────────────────────────────────────────

def check_censor(clock: str, work: str) -> None:
    from auto_uploader.utils import censor

    words = words_every_second()
    segments = [{"id": 0, "start": 0.0, "end": float(SECONDS),
                 "text": " ".join(w["word"].strip() for w in words),
                 "words": words}]
    base = os.path.splitext(os.path.basename(clock))[0]
    with open(censor.words_cache_path(work, base), "w", encoding="utf-8") as fh:
        json.dump({"segments": segments, **censor._source_stamp(clock)}, fh)

    result = censor.censor_video(clock, work, bleep_method="silence")

    check(result.was_censored, "the flagged word was not censored at all")
    check(result.violation_count == 1,
          f"{result.violation_count} violations found, expected 1")

    pad = PAD_MS / 1000.0
    inside = mean_db(result.output_path, FLAG_AT - pad + 0.15,
                     FLAG_AT + 0.5 + pad - 0.15)
    before = mean_db(result.output_path, FLAG_AT - 1.0, FLAG_AT - pad - 0.1)
    after = mean_db(result.output_path, FLAG_AT + 0.5 + pad + 0.1, FLAG_AT + 1.5)

    print(f"  muted window        {inside:8.2f} dB")
    print(f"  the second before   {before:8.2f} dB")
    print(f"  the second after    {after:8.2f} dB")

    check(inside < min(before, after) - 30,
          f"the flagged window is only {min(before, after) - inside:.1f} dB "
          f"quieter than its neighbours - that is not silence")
    check(before > -60 and after > -60,
          "the audio around the flagged word was silenced too")

    src, out = duration(clock), duration(result.output_path)
    print(f"  duration            {src:8.2f}s -> {out:.2f}s")
    check(abs(src - out) < 0.5,
          f"censoring changed the length: {src:.2f}s -> {out:.2f}s")
    check(dimensions(result.output_path) == f"{WIDTH},{HEIGHT}",
          f"the picture was re-encoded to {dimensions(result.output_path)}")


def check_framing_and_cut(clock: str, work: str) -> None:
    from autoreel.clip_maker import ClipSpec, render_clip
    from autoreel.crop_strategy import CROP_CENTER, CROP_FIT, CROP_MOTION

    cases = [("center", CROP_CENTER, 8.0, 12.0),
             ("fit", CROP_FIT, 3.0, 6.0),
             ("motion", CROP_MOTION, 14.0, 17.0)]

    print(f"  {'strategy':10s} {'size':11s} {'length':>7s} {'picture@':>9s} "
          f"{'sound@':>7s} {'asked':>6s}")
    for label, strategy, start, end in cases:
        spec = ClipSpec(start=start, end=end, index=1, title=f"{label} clip")
        try:
            path = render_clip(clock, spec, os.path.join(work, f"{label}.mp4"),
                               strategy=strategy, watermark=False)
        except Exception as exc:
            check(False, f"{label}: render raised {type(exc).__name__}: {exc}")
            continue

        size, length = dimensions(path), duration(path)
        picture = second_from_picture(path, 0.5)
        sound = second_from_sound(path, 0.4)
        want = int(start)
        print(f"  {label:10s} {size:11s} {length:6.2f}s {str(picture):>9s} "
              f"{str(sound):>7s} {want:>6d}")

        check(size == "1080,1920", f"{label}: rendered {size}, not 1080x1920")
        check(abs(length - spec.duration) < 0.35,
              f"{label}: {length:.2f}s long, asked for {spec.duration:.2f}s")
        check(picture == want,
              f"{label}: picture starts at source second {picture}, "
              f"asked for {want}")
        check(sound == want,
              f"{label}: SOUND starts at source second {sound} while the "
              f"picture starts at {picture} - they are out of sync")


def check_caption_sync(clock: str, work: str) -> None:
    import numpy as np

    from autoreel.captions import caption_file_for_clip
    from autoreel.clip_maker import ClipSpec, render_clip
    from autoreel.crop_strategy import CROP_CENTER

    # Speech at source seconds 8-11 and nowhere else, so the last two
    # seconds of the clip must come back bare.
    spoken = (8, 9, 10, 11)
    words = [{"word": f" word{i}", "start": float(i), "end": float(i) + 0.6,
              "probability": 0.95} for i in spoken]
    segments = [{"id": 0, "start": 8.0, "end": 12.0,
                 "text": " ".join(w["word"].strip() for w in words),
                 "words": words}]

    start, end = 8.0, 14.0
    spec = ClipSpec(start=start, end=end, index=1, title="Caption sync")
    ass = caption_file_for_clip(os.path.join(work, "sync.ass"), segments,
                                start, end)
    if not check(bool(ass), "no caption file was written at all"):
        return

    stamps = []
    for line in open(ass, encoding="utf-8"):
        found = re.match(r"Dialogue:\s*\d+,([\d:.]+),([\d:.]+),", line)
        if found:
            def to_seconds(stamp: str) -> float:
                hours, minutes, secs = stamp.split(":")
                return int(hours) * 3600 + int(minutes) * 60 + float(secs)
            stamps.append((to_seconds(found.group(1)),
                           to_seconds(found.group(2))))

    if not check(bool(stamps), "the caption file has no dialogue lines"):
        return
    print(f"  {len(stamps)} caption lines, {stamps[0][0]:.2f}s -> "
          f"{stamps[-1][1]:.2f}s of a {end - start:.0f}s clip")

    check(stamps[0][0] < 2.0,
          f"captions start at {stamps[0][0]:.2f}s of a clip that begins at 0 "
          f"- the source timestamps were never rebased, so nothing shows")
    check(stamps[-1][1] <= (end - start) + 0.5,
          f"a caption runs to {stamps[-1][1]:.2f}s, past the end of the clip")

    plain = render_clip(clock, spec, os.path.join(work, "plain.mp4"),
                        strategy=CROP_CENTER, watermark=False)
    burned = render_clip(clock, spec, os.path.join(work, "burned.mp4"),
                         strategy=CROP_CENTER, caption_path=ass,
                         watermark=False)

    for at in (0.3, 1.3, 2.3, 3.3, 4.5, 5.5):
        bare, painted = frame_gray(plain, at), frame_gray(burned, at)
        size = min(bare.size, painted.size)
        changed = (float(np.mean(np.abs(bare[:size] - painted[:size]) > 8))
                   if size else 0.0)
        scheduled = any(lo <= at <= hi for lo, hi in stamps)
        if scheduled:
            check(changed > 0.001,
                  f"t={at}s: a caption is timed here but the frame is bare")
        else:
            check(changed < 0.02,
                  f"t={at}s: {changed * 100:.1f}% of the frame is painted "
                  f"with no caption scheduled")


def check_caption_censoring(work: str) -> None:
    """The audio pass mutes it; printing it underneath in yellow undoes
    the whole thing."""
    from autoreel.captions import caption_file_for_clip

    words = [{"word": w, "start": 8.0 + i, "end": 8.6 + i, "probability": 0.95}
             for i, w in enumerate([" this", " is", f" {FLAG_WORD}", " okay"])]
    segments = [{"id": 0, "start": 8.0, "end": 12.0,
                 "text": f"this is {FLAG_WORD} okay", "words": words}]

    path = caption_file_for_clip(os.path.join(work, "flagged.ass"), segments,
                                 8.0, 12.0)
    if not check(bool(path), "no caption file for the flagged clip"):
        return
    text = open(path, encoding="utf-8").read().lower()
    check(FLAG_WORD not in text,
          f"the muted word is printed in the captions underneath it")
    print(f"  the muted word is {'ABSENT' if FLAG_WORD not in text else 'PRESENT'} "
          f"from the caption text")


# ── driver ───────────────────────────────────────────────────────────────

STAGES = [
    ("CENSOR    a flagged word is silenced and nothing else is touched",
     check_censor, True),
    ("FRAMING   every crop gives 1080x1920, cut where it was asked",
     check_framing_and_cut, True),
    ("CAPTIONS  rebased to clip time, painted only over speech",
     check_caption_sync, True),
    ("CAPTION   a muted word is not printed underneath it",
     check_caption_censoring, False),
]


def main() -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg and ffprobe have to be on PATH for this check.")
        return 2
    try:
        import numpy  # noqa: F401
    except ImportError:
        print("numpy is needed to measure the output: pip install numpy")
        return 2

    work = tempfile.mkdtemp(prefix="autobleep_e2e_")
    print("Building test footage - 20 seconds, one colour and one tone per "
          "second...")
    try:
        clock = build_clock(work)
        for title, stage, needs_clock in STAGES:
            print(f"\n{title}")
            before = len(_FAILURES)
            try:
                stage(clock, work) if needs_clock else stage(work)
            except Exception as exc:
                check(False, f"{title.split()[0]} raised "
                             f"{type(exc).__name__}: {exc}")
            if len(_FAILURES) == before:
                print("  ok")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAILED - {len(_FAILURES)} of {_CHECKS} checks")
        for failure in _FAILURES:
            print(f"  * {failure}")
        return 1
    print(f"PASSED - all {_CHECKS} checks, on real video, with real ffmpeg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

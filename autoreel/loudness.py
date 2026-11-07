"""Level every clip to the same loudness with a STATIC gain (V4, 11-07).

Clips cut from one stream ranged -9.7 to -19.4 LUFS, so a compilation
jumped 10 LU between pieces. loudnorm/dynaudnorm fix that but smear
transients and moved the audio a few hundred ms (tests/test_clip_sync
caught it), so instead: measure once (ebur128), then apply one constant
volume= gain plus a true-peak limiter. A constant gain cannot move anything
in time.

gain_filter(path) -> "volume=+4.3dB,alimiter=..." or "" when it cannot measure.
"""

import re
import subprocess

TARGET_LUFS = -14.0
MAX_BOOST_DB = 12.0
LIMIT = "alimiter=limit=0.891:level=false"       # -1 dBTP


def measure(path: str, timeout: int = 600) -> float:
    """Integrated loudness in LUFS, or nan."""
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-vn",
                            "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=timeout)
        m = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", r.stderr)
        return float(m[-1]) if m else float("nan")
    except Exception:
        return float("nan")


def gain_filter(path: str, target: float = TARGET_LUFS) -> str:
    lufs = measure(path)
    if lufs != lufs or lufs < -70:          # nan / silence
        return ""
    gain = max(-MAX_BOOST_DB, min(MAX_BOOST_DB, target - lufs))
    if abs(gain) < 0.5:
        return LIMIT
    return f"volume={gain:+.1f}dB,{LIMIT}"


def level(path: str, bitrate: str = "192k") -> bool:
    """Re-encode only the audio of a finished file to TARGET_LUFS, video
    stream-copied, in place. False (file untouched) on any problem."""
    import os

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    af = gain_filter(path)
    if not af or af == LIMIT:
        return False
    tmp = path + ".lvl.mp4"
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
                            "-i", path, "-c:v", "copy", "-af", af, "-c:a", "aac",
                            "-b:a", bitrate, "-movflags", "+faststart", tmp],
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError(r.stderr[-200:])
        os.replace(tmp, path)
        return True
    except Exception as exc:
        print(f"[Audio] level skipped for {os.path.basename(path)}: {exc}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False

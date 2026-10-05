"""Use the NVIDIA card for video encoding wherever the pipeline encodes.

One cached probe, shared by every module in autoreel/. The probe frame is
320x240 on purpose: NVENC refuses frames under ~145px wide, and the old
128x128 probes failed on a working RTX 4060, silently sending every
encode to the CPU (measured ~4x slower on a color-graded 1080p VOD).
"""

import shutil
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    try:
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.1",
                            "-c:v", "h264_nvenc", "-f", "null", "-"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def best_h264_encoder(preference: str = "auto") -> str:
    """'h264_nvenc' when the GPU can encode, else 'libx264'.

    preference: 'auto' (default) or 'nvenc' -> GPU if it works;
    'cpu' / 'libx264' -> always the CPU.
    """
    p = (preference or "auto").strip().lower()
    if p in ("cpu", "libx264"):
        return "libx264"
    if p == "h264_nvenc" or p in ("auto", "nvenc", "gpu"):
        return "h264_nvenc" if nvenc_available() else "libx264"
    return "libx264"

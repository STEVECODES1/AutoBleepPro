"""
Health checks + temp cleanup for the upload pipeline (CLI: --health).

Core checks use only the stdlib (shutil.disk_usage) so they always work;
CPU/RAM stats come from psutil when it's installed and degrade to
"unavailable" when it isn't. Alerts go through utils.notifier, same as
upload events.
"""

import glob
import os
import shutil
import time
import urllib.request

from .notifier import notify

# Reachability probes for the two services uploads depend on. Any HTTP
# response (even 403 - Rumble's Cloudflare challenges non-browser clients)
# proves the network path works; only a connection/DNS failure counts as
# unreachable.
_PROBES = (
    ("YouTube API", "https://www.googleapis.com/generate_204"),
    ("Rumble", "https://rumble.com/"),
)


def _probe(url: str, timeout: int = 10) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        urllib.request.urlopen(request, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # got an HTTP status back - the network path works
    except Exception:
        return False


def cleanup_temps(cfg, max_age_days: int = 7) -> list:
    """Delete stale working files this pipeline created. Returns the paths
    removed. Only touches files matching OUR naming patterns - never
    arbitrary contents of any folder."""
    removed = []
    cutoff = time.time() - max_age_days * 86400

    patterns = [
        os.path.join(cfg.general.logs_folder, "rumble_page_dump_*.html"),
        os.path.join(cfg.general.censored_folder, "*_CENSORED_*.mp4"),
        os.path.join(cfg.general.censored_folder, "_*_audio*.wav"),
    ]
    for pattern in patterns:
        for path in glob.glob(pattern):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed.append(path)
            except OSError:
                continue

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for pycache in glob.glob(os.path.join(project_root, "**", "__pycache__"), recursive=True):
        try:
            shutil.rmtree(pycache)
            removed.append(pycache)
        except OSError:
            continue
    return removed


def disk_free_gb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / 2**30
    except OSError:
        return -1.0


def run_health_check(cfg, features: dict = None, do_cleanup: bool = True) -> bool:
    """Print a health report; returns True when everything looks OK.
    Problems also raise a desktop notification."""
    features = features or {}
    min_free_gb = float(features.get("min_free_gb", 10))
    max_age_days = int(features.get("cleanup_age_days", 7))
    problems = []

    print("=== Health check ===")

    for label, path in (("watch folder", cfg.general.watch_folder),
                        ("project drive", cfg.project_root)):
        free = disk_free_gb(path)
        status = "??" if free < 0 else f"{free:.1f} GB free"
        print(f"  Disk ({label:13s}): {status}")
        if 0 <= free < min_free_gb:
            problems.append(f"Low disk on {label}: {free:.1f} GB free (< {min_free_gb} GB)")

    try:
        import psutil
        print(f"  CPU: {psutil.cpu_percent(interval=0.5):.0f}%   "
              f"RAM: {psutil.virtual_memory().percent:.0f}% used")
    except ImportError:
        print("  CPU/RAM: psutil not installed (pip install psutil) - skipped")

    # Face framing is the difference between a Monkey clip centred on the
    # person and one centred on the wall beside them, and without
    # mediapipe it turns itself off silently - every clip falls back to a
    # fixed rectangle and drifts out of it, with nothing anywhere saying
    # why. Not a "problem": clips still render, and gameplay never wanted
    # it. But it must be visible.
    try:
        from autoreel.face_region import have_mediapipe

        if have_mediapipe():
            print("  Face framing: mediapipe present - Monkey clips will "
                  "follow the people")
        else:
            print("  Face framing: mediapipe MISSING - Monkey clips fall "
                  "back to a fixed rectangle")
            print("                fix with:  pip install mediapipe")
    except Exception as exc:
        print(f"  Face framing: could not be checked ({exc})")

    for name, url in _PROBES:
        ok = _probe(url)
        print(f"  Network ({name}): {'reachable' if ok else 'UNREACHABLE'}")
        if not ok:
            problems.append(f"{name} unreachable")

    if do_cleanup:
        removed = cleanup_temps(cfg, max_age_days)
        print(f"  Cleanup: removed {len(removed)} stale temp file(s)/cache dir(s)")

    if problems:
        for p in problems:
            print(f"  !! {p}")
        notify("Upload pipeline health", "; ".join(problems),
               cfg.general.enable_desktop_notifications)
        return False
    print("  All checks passed.")
    return True

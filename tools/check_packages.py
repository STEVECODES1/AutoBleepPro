"""Does every package AutoBleep needs actually IMPORT, and the tools run?

After a fresh Windows (10-28) or a cleaner tool deleting files out of
site-packages (CCleaner took better_profanity\\alphabetic_unicode.json),
"pip says it is installed" is not enough. This imports each one, checks
the data files that went missing before, the command-line tools on PATH and
the GPU, and prints one line per item.

    python -X utf8 tools/check_packages.py            report only
    python -X utf8 tools/check_packages.py --repair   force-reinstall what is broken

Exit 0 when everything required works, 1 when something does not.
"""
import argparse
import importlib
import os
import shutil
import subprocess
import sys

# import name -> pip package that puts it back. REQUIRED stops the program.
REQUIRED = {
    "dotenv": "python-dotenv",
    "googleapiclient.discovery": "google-api-python-client",
    "googleapiclient.http": "google-api-python-client",
    "google_auth_oauthlib": "google-auth-oauthlib",
    "google.auth": "google-auth",
    "playwright.sync_api": "playwright",
    "watchdog.observers": "watchdog",
    "requests": "requests",
    "pydub": "pydub==0.25.1",
    "yt_dlp": "yt-dlp[default,curl-cffi]",
    "curl_cffi": "curl_cffi",
    "better_profanity": "better-profanity==0.7.0",
    "pyparsing": "pyparsing",
    "numpy": "numpy<2.0",
    "scipy": "scipy==1.11.4",
    "PIL.Image": "Pillow==10.3.0",
    "psutil": "psutil==7.2.2",
    "faster_whisper": "faster-whisper==1.1.1",
    "ctranslate2": "ctranslate2",
    "stable_whisper": "stable-ts[fw]==2.19.1",
    "whisper": "openai-whisper",
    "moviepy": "moviepy==2.0.0",
    "tqdm": "tqdm==4.66.4",
}
# Absent, these cost a feature, not the program.
OPTIONAL = {
    "torch": "torch",
    "torchaudio": "torchaudio",
    "mediapipe": "mediapipe",
    "demucs": "demucs",
    "panns_inference": "panns_inference",
    "customtkinter": "customtkinter==5.2.2",
    "tweepy": "tweepy",
    "anthropic": "anthropic",
    "twelvelabs": "twelvelabs",
    "plyer": "plyer==2.1.0",
    "cv2": "opencv-python",
}
TOOLS = ("ffmpeg", "ffprobe", "deno", "git")


def try_import(name):
    try:
        importlib.import_module(name)
        return ""
    except Exception as exc:  # noqa: BLE001 - any failure means unusable
        return f"{type(exc).__name__}: {str(exc)[:120]}"


def profanity_data_ok():
    """The data files CCleaner deleted; the import alone can miss them."""
    try:
        import better_profanity

        folder = os.path.dirname(better_profanity.__file__)
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    gone = [f for f in ("alphabetic_unicode.json", "profanity_wordlist.txt")
            if not os.path.isfile(os.path.join(folder, f))]
    return ("missing " + ", ".join(gone)) if gone else ""


def gpu_report():
    lines = []
    try:
        import torch

        lines.append(f"torch CUDA: {torch.cuda.is_available()}"
                     + (f" ({torch.cuda.get_device_name(0)})"
                        if torch.cuda.is_available() else ""))
    except Exception as exc:  # noqa: BLE001
        lines.append(f"torch: {type(exc).__name__}")
    try:
        import ctranslate2

        lines.append(f"faster-whisper GPUs: {ctranslate2.get_cuda_device_count()}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"ctranslate2: {type(exc).__name__}")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args(argv)

    broken, weak = {}, {}
    for name, pkg in REQUIRED.items():
        why = try_import(name)
        print(f"{'OK  ' if not why else 'BAD '} {name}" + (f"  - {why}" if why else ""))
        if why:
            broken[name] = pkg
    why = profanity_data_ok()
    print(f"{'OK  ' if not why else 'BAD '} better_profanity data files"
          + (f"  - {why}" if why else ""))
    if why:
        broken["better_profanity (data)"] = REQUIRED["better_profanity"]
    for name, pkg in OPTIONAL.items():
        why = try_import(name)
        print(f"{'OK  ' if not why else 'MISS'} {name} (optional)"
              + (f"  - {why}" if why else ""))
        if why:
            weak[name] = pkg
    for tool in TOOLS:
        where = shutil.which(tool)
        print(f"{'OK  ' if where else 'BAD '} {tool}" + (f"  {where}" if where else "  - not on PATH"))
    for line in gpu_report():
        print(f"GPU  {line}")

    if args.repair and (broken or weak):
        packages = sorted(set(broken.values()) | set(weak.values()))
        print("Repairing: " + " ".join(packages))
        subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall",
                        "--no-deps", "--no-cache-dir", *packages])
        subprocess.run([sys.executable, "-m", "pip", "install", *packages])
        importlib.invalidate_caches()
        still = [n for n in broken if not n.endswith("(data)") and try_import(n)]
        if profanity_data_ok():
            still.append("better_profanity (data)")
        print("Still broken: " + (", ".join(still) if still else "nothing"))
        return 1 if still else 0
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())

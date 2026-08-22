"""What has to be installed, checked before anything tries to import it.

The uploader died in a restart loop with

    ModuleNotFoundError: No module named 'dotenv'

and the reason it could happen at all is that INSTALL.bat installed
`requirements.txt` at the repo root, which is AutoReel's list. Everything
the UPLOADER needs - dotenv, the Google API clients, playwright, watchdog,
yt-dlp - lives in auto_uploader/requirements.txt, and nothing ever
installed that. A machine that has only ever run INSTALL.bat cannot run
the uploader, and the failure arrives as a bare traceback fifteen seconds
apart, forever.

INSTALL.bat installs both lists now. This module is the second line: it
runs before the imports that would fail, says plainly what is missing,
and installs it rather than making someone read a traceback and work out
the pip command.

Installing is bounded. A stamp file records the attempt, and a second one
inside the cooldown reports instead of retrying - otherwise a keepalive
loop reinstalls the world every fifteen seconds against a dead network.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_UPLOADER = os.path.dirname(_HERE)
_REPO = os.path.dirname(_UPLOADER)

REQUIREMENT_FILES = (
    os.path.join(_REPO, "requirements.txt"),
    os.path.join(_UPLOADER, "requirements.txt"),
)

# import name -> what to install. They differ often enough that guessing
# produces "pip install dotenv", which installs a different, abandoned
# package and leaves the error in place.
REQUIRED = {
    "dotenv": "python-dotenv",
    "googleapiclient": "google-api-python-client",
    "google_auth_oauthlib": "google-auth-oauthlib",
    "playwright": "playwright",
    "watchdog": "watchdog",
    "requests": "requests",
    "pydub": "pydub",
    "yt_dlp": "yt-dlp[default,curl-cffi]",
}

if sys.version_info >= (3, 13):
    # audioop left the standard library in 3.13 (PEP 594). pydub imports
    # it, falls back to `pyaudioop`, and that has never existed - so the
    # censor pass dies naming a module nobody installed on purpose:
    #
    #     ModuleNotFoundError: No module named 'pyaudioop'
    #
    # Checked here so it is reported as a missing package with a fix,
    # rather than as a puzzle in the middle of censoring a stream.
    REQUIRED["audioop"] = "audioop-lts"


# Absent, these cost a feature. Absent, the ones above cost the program.
OPTIONAL = {
    "plyer": "plyer",           # desktop notifications
    "psutil": "psutil",         # CPU/RAM in --health
}

COOLDOWN_SECONDS = 30 * 60
EXIT_MISSING_DEPS = 3


def _stamp_path() -> str:
    return os.path.join(_UPLOADER, "logs", ".deps_install_attempt")


def missing(names: dict) -> list[str]:
    """Import names that cannot be found.

    find_spec locates without executing, so a heavy package costs nothing
    to check and a broken one does not raise here.
    """
    absent = []
    for module in names:
        try:
            if importlib.util.find_spec(module) is None:
                absent.append(module)
        except (ImportError, ValueError):
            absent.append(module)
    return absent


def _recently_attempted() -> bool:
    try:
        age = time.time() - os.path.getmtime(_stamp_path())
    except OSError:
        return False
    return age < COOLDOWN_SECONDS


def _record_attempt() -> None:
    path = _stamp_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(time.time()))
    except OSError:
        pass


def install_command() -> list[str]:
    """Always through this interpreter. A bare `pip` on a machine with
    two Pythons installs into the other one, and the import fails again
    with the packages visibly present."""
    command = [sys.executable, "-m", "pip", "install"]
    for path in REQUIREMENT_FILES:
        if os.path.exists(path):
            command += ["-r", path]
    return command


def manual_hint() -> str:
    return " ".join(install_command())


def install() -> bool:
    command = install_command()
    print(f"[Deps] Installing: {' '.join(command[3:])}")
    print("[Deps] This takes a few minutes the first time.")
    try:
        result = subprocess.run(command)
    except Exception as exc:
        print(f"[Deps] Could not run pip: {exc}")
        return False
    return result.returncode == 0


def ensure(auto: bool = True) -> None:
    """Called before the imports that need any of this.

    Returns quietly when everything is present, which is every run but
    the broken one.
    """
    absent = missing(REQUIRED)
    if not absent:
        for module in missing(OPTIONAL):
            print(f"[Deps] {OPTIONAL[module]} is not installed - "
                  f"{'desktop notifications' if module == 'plyer' else 'CPU/RAM in --health'} "
                  f"will be skipped.")
        return

    packages = sorted(REQUIRED[m] for m in absent)
    print("=" * 60)
    print(f"[Deps] MISSING: {', '.join(packages)}")
    print("[Deps] The uploader cannot start without these.")
    print("=" * 60)

    if not auto:
        print(f"[Deps] Install them with:\n    {manual_hint()}")
        raise SystemExit(EXIT_MISSING_DEPS)

    if _recently_attempted():
        # Installing again inside the cooldown means the last attempt
        # did not work, and a keepalive loop would repeat it forever.
        print("[Deps] Already tried installing recently and they are still "
              "missing. Not retrying.")
        print(f"[Deps] Run this yourself and read what it says:\n"
              f"    {manual_hint()}")
        raise SystemExit(EXIT_MISSING_DEPS)

    _record_attempt()
    ok = install()
    importlib.invalidate_caches()
    still = missing(REQUIRED)

    if ok and not still:
        print("[Deps] Installed. Carrying on.")
        return

    if still:
        print(f"[Deps] Still missing after installing: "
              f"{', '.join(sorted(REQUIRED[m] for m in still))}")
    print(f"[Deps] Run this yourself and read what it says:\n"
          f"    {manual_hint()}")
    raise SystemExit(EXIT_MISSING_DEPS)


def _report() -> int:
    """`python utils/deps.py --check` - what INSTALL.bat calls at the end
    to prove the install actually worked, rather than assuming pip's exit
    code covered it."""
    absent = missing(REQUIRED)
    for module, package in sorted(REQUIRED.items()):
        state = "MISSING" if module in absent else "ok"
        print(f"  {state:<14s}{package}")
    absent_optional = missing(OPTIONAL)
    for module, package in sorted(OPTIONAL.items()):
        state = "not installed" if module in absent_optional else "ok"
        print(f"  {state:<14s}{package} (optional)")
    if absent:
        print(f"\n{len(absent)} required package(s) missing.")
        return 1
    print("\nEverything the uploader imports is installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_report())

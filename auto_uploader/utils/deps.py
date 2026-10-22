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

# Packages that were PRESENT but half-deleted on 10-20: find_spec found
# them, so the old check passed, and the uploader died on import in a
# keepalive loop for two days. The failure was in a data file inside
# better_profanity and in pyparsing/numpy/Pillow/googleapiclient. An
# import probe is the only thing that catches a package that exists but
# cannot be used.
#
# name -> the pip package that puts it back. Pillow is capped below 11
# because moviepy 2.0 requires it; the requirements file says the same.
IMPORT_PROBES = {
    "pyparsing": "pyparsing",
    "googleapiclient.http": "google-api-python-client",
    "google.auth": "google-auth",
    "better_profanity": "better_profanity==0.7.0",
    "numpy": "numpy",
    "PIL.Image": "Pillow<11",
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


def broken_imports() -> dict[str, str]:
    """Installed modules that cannot actually be imported.

    A package directory can survive a failed update, an antivirus
    quarantine or a half-finished pip run while its modules are gone.
    find_spec says "present"; the first real import raises - and the
    keepalive repeats it every fifteen seconds forever.
    """
    broken: dict[str, str] = {}
    for module in IMPORT_PROBES:
        try:
            importlib.import_module(module)
        except Exception as exc:
            broken[module] = f"{type(exc).__name__}: {exc}"
    return broken


def repair_command(broken: dict[str, str] | None = None) -> list[str]:
    """pip command that rebuilds exactly the broken packages."""
    broken = broken if broken is not None else broken_imports()
    packages = sorted({IMPORT_PROBES[module] for module in broken})
    return [sys.executable, "-m", "pip", "install", "--force-reinstall",
            "--no-cache-dir", *packages]


def repair(broken: dict[str, str] | None = None) -> bool:
    """Force-reinstall the broken packages. True if pip accepted it."""
    command = repair_command(broken)
    print(f"[Deps] Repairing: {' '.join(command[4:])}")
    print("[Deps] This can take a few minutes; it only runs on a broken "
          "install.")
    try:
        result = subprocess.run(command)
    except Exception as exc:
        print(f"[Deps] Could not run pip: {exc}")
        return False
    importlib.invalidate_caches()
    return result.returncode == 0


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
    broken = broken_imports()
    if not absent and not broken:
        for module in missing(OPTIONAL):
            print(f"[Deps] {OPTIONAL[module]} is not installed - "
                  f"{'desktop notifications' if module == 'plyer' else 'CPU/RAM in --health'} "
                  f"will be skipped.")
        return

    packages = sorted(REQUIRED[m] for m in absent)
    print("=" * 60)
    if absent:
        print(f"[Deps] MISSING: {', '.join(packages)}")
    if broken:
        print(f"[Deps] INSTALLED BUT BROKEN: {', '.join(sorted(broken))}")
        for module, why in sorted(broken.items()):
            print(f"        {module}: {why}")
        print("[Deps] The site-packages copy is incomplete (a failed "
              "update, a quarantine, a half-finished pip run).")
    print("[Deps] The uploader cannot start without these.")
    print("=" * 60)

    if not auto:
        if absent:
            print(f"[Deps] Install the missing ones with:\n    {manual_hint()}")
        if broken:
            print(f"[Deps] Repair the broken ones with:\n    "
                  f"{' '.join(repair_command(broken))}")
        raise SystemExit(EXIT_MISSING_DEPS)

    if _recently_attempted():
        # Installing again inside the cooldown means the last attempt
        # did not work, and a keepalive loop would repeat it forever.
        print("[Deps] Already tried installing/repairing recently and they "
              "are still broken. Not retrying.")
        print(f"[Deps] Run this yourself and read what it says:\n"
              f"    {manual_hint()}")
        raise SystemExit(EXIT_MISSING_DEPS)

    _record_attempt()
    ok = True
    if absent:
        ok = install()
        importlib.invalidate_caches()
        still = missing(REQUIRED)
        if still:
            ok = False
            print(f"[Deps] Still missing after installing: "
                  f"{', '.join(sorted(REQUIRED[m] for m in still))}")
    broken = broken_imports()
    if broken:
        repaired = repair(broken)
        if not repaired:
            ok = False
        broken = broken_imports()
        if broken:
            ok = False
            print(f"[Deps] Still broken after repairing: "
                  f"{', '.join(sorted(broken))}")

    if ok and not broken:
        print("[Deps] Repaired. Carrying on.")
        return

    print(f"[Deps] Run this yourself and read what it says:\n"
          f"    {manual_hint()}")
    raise SystemExit(EXIT_MISSING_DEPS)


def _report() -> int:
    """`python utils/deps.py --check` - what INSTALL.bat calls at the end
    to prove the install actually worked, rather than assuming pip's exit
    code covered it."""
    absent = missing(REQUIRED)
    broken = broken_imports()
    for module, package in sorted(REQUIRED.items()):
        state = "MISSING" if module in absent else "ok"
        print(f"  {state:<14s}{package}")
    absent_optional = missing(OPTIONAL)
    for module, package in sorted(OPTIONAL.items()):
        state = "not installed" if module in absent_optional else "ok"
        print(f"  {state:<14s}{package} (optional)")
    for module, why in sorted(broken.items()):
        print(f"  BROKEN        {module} ({IMPORT_PROBES[module]}): {why}")
    if absent or broken:
        if absent:
            print(f"\n{len(absent)} required package(s) missing.")
        if broken:
            print(f"\n{len(broken)} installed package(s) cannot be imported.")
        print(f"Repair with:\n    {' '.join(repair_command(broken))}")
        return 1
    print("\nEverything the uploader imports is installed and imports cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_report())

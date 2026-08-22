"""Are the links still good? Answered in about twenty seconds.

The recorder watches five URLs and says "Not live yet" about all of them
whether the channel is quiet or the handle is wrong. Those look identical
in the window and mean opposite things: one is a normal Tuesday, the
other is a stream that will never be recorded no matter how long it waits.

This tells them apart:

  LIVE        streaming right now
  offline     the channel is real and quiet - nothing to do
  NOT FOUND   the handle does not resolve. Recording it is impossible and
              the recorder will wait for it forever without saying so
  blocked     the site refused THIS machine - Cloudflare, a bot check, no
              network. Not a config problem

The URLs are read out of _RUN_RECORDER.bat rather than written down again
here, because a checker that tests a different list from the one being
watched is worse than no checker.

    python tools/check_links.py
    python tools/check_links.py --record-test        capture from whatever
                                                     is live, then delete it
    python tools/check_links.py --record-test URL    or from a URL you name

--record-test is the part no amount of metadata proves: it records a few
seconds for real, checks the file has video and audio in it, and deletes
it. Nothing it writes survives the run, and it never touches config.json,
the watch folder or anything already recorded.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
for _path in (_REPO, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from record_stream import YTDLP, is_clips_url, platform_of  # noqa: E402

RECORDER_BAT = os.path.join(_REPO, "_RUN_RECORDER.bat")

LIVE = "LIVE"
OFFLINE = "offline"
NOT_FOUND = "NOT FOUND"
BLOCKED = "blocked"
EMPTY = "empty"

# What yt-dlp says, and what it actually means. Order matters: a bot
# check also contains the word "download", and a channel that is merely
# quiet must never be reported as missing.
MEANINGS = (
    (OFFLINE, ("is not currently live", "not currently live",
               "no video formats found", "the channel is not live")),
    (BLOCKED, ("sign in to confirm", "confirm you're not a bot",
               "confirm you’re not a bot", "failed to perform",
               "connection reset", "ssl", "cloudflare", "403", "429",
               "temporarily blocked", "unable to download json metadata",
               "unable to download webpage")),
    (NOT_FOUND, ("does not exist", "not found", "404", "unable to recognize",
                 "no such channel", "this channel does not", "is unavailable",
                 "account has been suspended", "has been terminated")),
)

HEALTHY = {LIVE, OFFLINE, EMPTY}


def classify(returncode: int, output: str) -> tuple[str, str]:
    """(state, the line worth showing)."""
    lowered = output.lower()
    for state, needles in MEANINGS:
        for needle in needles:
            if needle in lowered:
                return state, _worst_line(output)
    if returncode == 0:
        return LIVE, ""
    return NOT_FOUND, _worst_line(output)


def _worst_line(output: str) -> str:
    for line in reversed(output.strip().splitlines()):
        if line.strip().startswith("ERROR"):
            return line.strip()[:160]
    tail = output.strip().splitlines()
    return tail[-1].strip()[:160] if tail else ""


def watched_urls() -> list[str]:
    """Exactly what the recorder is told to watch."""
    try:
        with open(RECORDER_BAT, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
    except OSError:
        return []
    for line in body.splitlines():
        if "record_stream.py" in line and not line.strip().startswith("REM"):
            return re.findall(r'"(https?://[^"]+)"', line)
    return []


def probe(url: str, timeout: int = 75) -> tuple[str, str]:
    args = list(YTDLP) + ["--skip-download", "--no-warnings",
                          "--playlist-items", "1",
                          "--print", "%(id)s"]
    if is_clips_url(url):
        # A clips page is a playlist; an empty one is normal and is not a
        # broken link.
        args += ["--flat-playlist"]
    args.append(url)
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return BLOCKED, f"no answer in {timeout}s"
    except FileNotFoundError:
        return BLOCKED, "yt-dlp is not installed - run INSTALL.bat"

    combined = f"{done.stdout}\n{done.stderr}"
    if is_clips_url(url) and done.returncode == 0 and not done.stdout.strip():
        return EMPTY, "no clips in the window - normal"
    return classify(done.returncode, combined)


def record_test(url: str, seconds: int = 15) -> bool:
    """Capture a few seconds for real, prove the file, delete it.

    Written to a temp folder that is removed afterwards, never the watch
    folder - a test recording must not become something that gets
    uploaded.
    """
    workspace = tempfile.mkdtemp(prefix="autobleep_linktest_")
    target = os.path.join(workspace, "test.%(ext)s")
    print(f"\nRecording {seconds}s from {url}")
    print(f"  into {workspace} (deleted when this finishes)")

    args = list(YTDLP) + [
        "--no-warnings", "--no-part",
        "--downloader", "ffmpeg",
        "--downloader-args", f"ffmpeg_i:-t {seconds}",
        "-f", "b[height<=480]/b",
        "-o", target, url]
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=seconds * 6 + 90)
    except subprocess.TimeoutExpired:
        print("  FAILED - yt-dlp did not finish in time")
        shutil.rmtree(workspace, ignore_errors=True)
        return False
    except FileNotFoundError:
        print("  FAILED - yt-dlp is not installed")
        shutil.rmtree(workspace, ignore_errors=True)
        return False

    files = [os.path.join(workspace, f) for f in os.listdir(workspace)]
    ok = False
    if not files:
        print("  FAILED - nothing was written")
        print("  " + _worst_line(done.stdout + done.stderr))
    else:
        path = max(files, key=os.path.getsize)
        size = os.path.getsize(path)
        streams = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", path], capture_output=True, text=True).stdout
        kinds = {ln.strip() for ln in streams.splitlines() if ln.strip()}
        length = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
        print(f"  wrote {os.path.basename(path)}  {size / 1_000_000:.1f} MB  "
              f"{length or '?'}s  streams={sorted(kinds) or 'none'}")
        ok = size > 50_000 and "video" in kinds and "audio" in kinds
        if not ok:
            print("  FAILED - the file is missing video or audio, or is "
                  "too small to be a real capture")

    shutil.rmtree(workspace, ignore_errors=True)
    print(f"  deleted the test recording - "
          f"{'gone' if not os.path.exists(workspace) else 'COULD NOT DELETE'}")
    return ok


def js_runtime_warning() -> str:
    """yt-dlp now deprecates YouTube extraction with no JS runtime.

    A warning today, and the thing that stops YouTube recording working
    tomorrow - so it is worth saying before it bites rather than at 2am
    during a stream.

    Only Deno counts: yt-dlp enables that one by default and needs
    --js-runtimes to be told about node or bun, so having node installed
    does nothing on its own.
    """
    if shutil.which("deno"):
        return ""
    others = [name for name in ("node", "bun") if shutil.which(name)]
    hint = ("Install Deno from https://deno.com" if not others else
            f"{'/'.join(others)} is installed but yt-dlp only enables deno "
            f"by default - install Deno, or pass --js-runtimes")
    return ("no JavaScript runtime yt-dlp will use. It has deprecated "
            "YouTube extraction without one and some formats are already "
            f"unavailable. {hint}.")


def impersonation_report() -> tuple[bool, str]:
    """Can yt-dlp still pretend to be a browser?

    Kick sits behind Cloudflare and every request is refused - "HTTP Error
    403: Forbidden" - unless yt-dlp can present a real browser's TLS
    fingerprint, which it does through curl_cffi.

    The failure worth catching is the quiet one: curl_cffi installed and
    importing perfectly while yt-dlp reports every target as
    (unavailable), because the two versions do not match. Nothing warns.
    Kick simply stops recording, and it reads as Kick being difficult.

    This is why curl_cffi is no longer pinned by hand - yt-dlp's supported
    range moves, and a fixed pin goes stale without saying so. Measured
    here instead.
    """
    try:
        done = subprocess.run(list(YTDLP) + ["--list-impersonate-targets"],
                              capture_output=True, text=True, timeout=90)
    except Exception as exc:
        return False, f"could not ask yt-dlp: {exc}"

    rows = [ln for ln in done.stdout.splitlines()
            if ln.strip() and not ln.startswith(("[info]", "Client", "---"))]
    available = [r for r in rows if "unavailable" not in r.lower()]

    if not rows:
        return False, ("yt-dlp lists NO impersonate targets. Kick will come "
                       "back 403 on every request. Fix with:\n"
                       "        python -m pip install -U "
                       "\"yt-dlp[default,curl-cffi]\"")
    if not available:
        return False, (f"all {len(rows)} impersonate targets read "
                       f"(unavailable) - curl_cffi is installed but its "
                       f"version does not match this yt-dlp. Kick will 403. "
                       f"Fix with:\n        python -m pip install -U "
                       f"\"yt-dlp[default,curl-cffi]\"")
    return True, f"{len(available)} impersonate target(s) available"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-test", nargs="?", const="", default=None,
                        metavar="URL",
                        help="record a few seconds for real, then delete it")
    parser.add_argument("--seconds", type=int, default=15)
    args = parser.parse_args()

    urls = watched_urls()
    if not urls:
        print(f"Could not read any URLs out of {RECORDER_BAT}")
        return 2

    print("=" * 68)
    print("  Checking every link the recorder watches")
    print("=" * 68)

    results = []
    for url in urls:
        state, detail = probe(url)
        results.append((url, state, detail))
        mark = "  " if state in HEALTHY else "! "
        print(f"\n{mark}{state:<10s} [{platform_of(url)}] {url}")
        if detail:
            print(f"             {detail}")

    broken = [(u, s) for u, s, _ in results if s == NOT_FOUND]
    blocked = [(u, s) for u, s, _ in results if s == BLOCKED]
    live = [u for u, s, _ in results if s == LIVE]

    print("\n" + "=" * 68)
    if broken:
        print(f"  {len(broken)} link(s) DO NOT RESOLVE - the recorder will "
              f"wait on these forever:")
        for url, _ in broken:
            print(f"    {url}")
    if blocked:
        print(f"  {len(blocked)} link(s) refused this machine. That is "
              f"network or Cloudflare,")
        print("  not your settings - try again, or from another connection.")
    if not broken and not blocked:
        print(f"  All {len(results)} links resolve. "
              f"{len(live)} live right now.")

    ok_impersonate, impersonate = impersonation_report()
    kick = [u for u, _, _ in results if platform_of(u) == "kick"]
    if kick:
        print(f"\n  {'  ' if ok_impersonate else '! '}Cloudflare bypass "
              f"(Kick): {impersonate}")

    warning = js_runtime_warning()
    if warning:
        print(f"\n  NOTE: {warning}")

    if args.record_test is not None:
        target = args.record_test or (live[0] if live else "")
        if not target:
            print("\n  --record-test needs something that is actually live. "
                  "Nothing is right now - pass a URL, or run this again "
                  "during a stream.")
        elif not record_test(target, args.seconds):
            return 1

    print()
    return 1 if (broken or (kick and not ok_impersonate)) else 0


if __name__ == "__main__":
    raise SystemExit(main())

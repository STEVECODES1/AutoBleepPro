"""
Records live streams end to end, and survives the network.

YouTube, Twitch and Kick, several at once, all delivering into the same
watch folder. A /clips URL is downloaded rather than recorded - clips are
already finished videos - and an archive file stops the same clip being
fetched twice.

WHY THE PLAIN yt-dlp COMMAND KEEPS STOPPING EARLY
-------------------------------------------------
A multi-hour live stream is thousands of small HLS fragments fetched one
after another. yt-dlp's defaults give up after ten failed fragments, which
over four hours of home wifi is not a question of if. When it gives up
mid-stream you are left with a partial file and the rest of the stream is
simply gone.

Four things fix that, and all four matter:

1. **Infinite retries.** `--fragment-retries infinite` plus `--retries
   infinite`: a fragment that fails is retried rather than ending the
   recording. This alone accounts for most early stops.
2. **MPEG-TS, not MP4.** `--hls-use-mpegts` writes a container that is
   valid at every byte. An interrupted .mp4 usually will not play at all,
   because the index that tells a player where everything is
   (the moov atom) is written last and never gets written. A killed .ts
   plays right up to the moment it stopped, so a crash costs the tail of
   the stream instead of all of it.
3. **Resume while still live.** If yt-dlp dies anyway, the stream is
   usually still going. Restarting immediately picks it up again; the
   result is two segments with a small gap rather than one truncated file.
   Segments are concatenated when the stream ends.
4. **Patience about being live.** `--wait-for-video` polls instead of
   exiting, so this can sit open for days between streams.

The finished file is MOVED into watch_folder rather than written there:
--watch reacts to a file arriving, and a move is atomic, so the uploader
sees a complete file or no file - never one still being written.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# A stream that ends and restarts within this window is treated as one
# interrupted recording rather than two streams.
RESUME_WINDOW_S = 90
# Give up resuming after this many consecutive restarts; past this it is
# not a blip, and spinning forever would fill the disk with fragments.
MAX_RESUMES = 20

# A stream that was FOUND and produced nothing gets this many quick
# retries before the channel is written off as offline. A 503 or a dead
# manifest clears in seconds; if the stream really has ended, this costs
# a minute before going back to watching.
MAX_MISSED_TRIES = 3
MISSED_RETRY_SECONDS = 20

SAFE_CHARS = " -_.,'!()[]"

# While waiting for a channel to go live, say so this often instead of
# echoing yt-dlp's per-minute countdown. Long enough to be quiet
# overnight, short enough to prove the window is still alive.
WAIT_HEARTBEAT_S = 1800

# Lines that are true of the RUN, not of one channel. Every watcher runs
# on its own thread and would otherwise print them all at once.
_SAID_FOR_EVERYONE: set = set()
_EVERYONE_LOCK = threading.Lock()

# A five-hour stream is roughly this much at 1080p. Checked before
# starting, because running out of disk four hours in loses the lot.
BYTES_PER_HOUR_1080P = 3_500_000_000
MIN_FREE_HOURS = 6


class KeepAwake:
    """Stops Windows sleeping while a recording is running.

    This is the failure that looks most like "it just stopped": the
    machine suspends on its idle timer partway through a long stream, the
    download dies with it, and on wake there is a partial file and no
    explanation. A console window does not count as activity - Windows
    only knows a program needs the machine awake if it says so.

    SetThreadExecutionState with ES_SYSTEM_REQUIRED is that request. The
    display is deliberately left alone; keeping the screen on all night
    is not needed to keep downloading.
    """

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __init__(self) -> None:
        self.active = False

    def __enter__(self) -> "KeepAwake":
        if sys.platform != "win32":
            return self
        try:
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(
                self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
            self.active = True
        except Exception:
            pass
        return self

    def __exit__(self, *exc) -> None:
        if not self.active:
            return
        try:
            import ctypes

            # Drop back to normal power behaviour, or the machine never
            # sleeps again after this process exits.
            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)
        except Exception:
            pass


def free_bytes(path: str) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def disk_warning(path: str, hours: int = MIN_FREE_HOURS) -> str:
    """A warning if there is not obviously room, else ''."""
    needed = BYTES_PER_HOUR_1080P * hours
    free = free_bytes(path)
    if free and free < needed:
        return (f"only {free / 1e9:.0f} GB free where recordings are staged - "
                f"a {hours}-hour 1080p stream needs roughly "
                f"{needed / 1e9:.0f} GB. Recording will stop when the disk "
                "fills, and that looks exactly like a stream ending early.")
    return ""


# Below this fraction of the stream, the recording is reported as short.
# Not 1.0: joining segments loses a second or two at each seam, and a
# recording that starts a moment after the stream does is normal.
COVERAGE_OK = 0.98


def probe_duration(path: str) -> Optional[float]:
    """Seconds of media in a file, or None if it cannot be determined."""
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    try:
        return float(completed.stdout.decode().strip())
    except ValueError:
        return None


# How far audio may drift from video before it is worth saying so. One
# AAC frame is ~23ms and nobody can hear it; a tenth of a second is the
# point where lips stop matching words.
SYNC_TOLERANCE_S = 0.25

# Audio realigned to the video clock. `aresample=async=1` puts samples
# where their timestamps say they belong instead of end to end, and
# `apad` fills what is missing with silence so a lost run of audio
# fragments costs silence rather than a permanent offset.
#
# WHY THIS EXISTS
# A live recording drops fragments - four hours of home wifi guarantees
# it. Joined with `-c copy`, missing audio is simply absent: the audio
# track comes out SHORTER than the video, so everything after the gap
# plays early, forever, and each resume adds more. Measured on a
# two-segment test that mimics one lost run: 364ms of drift with copy,
# 31ms - one frame - with this.
#
# Only the audio is re-encoded. Video is copied, so there is no quality
# loss and no GPU time.
_SYNC_AUDIO = ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
               "-af", "aresample=async=1:first_pts=0,apad", "-shortest"]

# Start both streams at zero and drop ffmpeg's default mux delay, so the
# file does not open with a built-in offset.
_SYNC_MUX = ["-avoid_negative_ts", "make_zero", "-muxdelay", "0",
             "-muxpreload", "0"]


def stream_durations(path: str) -> dict:
    """{"video": seconds, "audio": seconds} for what is in this file."""
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,duration", "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    found = {}
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        parts = line.strip().split(",")
        if len(parts) < 2 or parts[0] not in ("video", "audio"):
            continue
        try:
            found.setdefault(parts[0], float(parts[1]))
        except ValueError:
            continue
    return found


def av_offset(path: str):
    """How far audio and video lengths disagree, in seconds, or None.

    A positive number means the audio track is SHORTER than the video -
    the shape a recording takes when fragments were lost.
    """
    found = stream_durations(path)
    if "video" not in found or "audio" not in found:
        return None
    return found["video"] - found["audio"]


def sync_report(path: str) -> str:
    """One line about whether this file's audio matches its picture."""
    offset = av_offset(path)
    if offset is None:
        return "Sync: could not measure (no audio or no video track)."
    if abs(offset) <= SYNC_TOLERANCE_S:
        return f"Sync: audio and video agree to {abs(offset) * 1000:.0f}ms."
    direction = "behind" if offset > 0 else "ahead of"
    return (f"Sync: WARNING - audio runs {abs(offset):.2f}s {direction} the "
            f"picture. The recording lost fragments this could not cover.")


def stream_title(url: str) -> str:
    """The title the streamer actually gave this stream, or "".

    Asked WHILE it is live, because afterwards a live URL resolves to
    nothing. Without it the uploader falls back to the filename, which is
    a timestamp - so a Kick stream called "WOW" was published as
    "Stackswopo kick live 2026-08-08 19_08".
    """
    try:
        completed = subprocess.run(
            YTDLP + ["--no-warnings", "--skip-download", "--print", "title", url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    title = completed.stdout.decode("utf-8", "replace").strip().splitlines()
    return title[0].strip() if title else ""


def expected_duration(url: str) -> Optional[float]:
    """How long the stream actually was, according to the platform."""
    try:
        completed = subprocess.run(
            YTDLP + ["--no-warnings", "--skip-download",
                     "--print", "duration", url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    try:
        return float(completed.stdout.decode().strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def ytdlp_command() -> list:
    """How to invoke yt-dlp, preferring the one in THIS interpreter.

    `yt-dlp` on PATH is often the standalone .exe, which bundles its own
    Python and cannot see site-packages. Installing curl_cffi - the
    dependency Kick needs to get past Cloudflare - therefore appears to
    work (it imports fine) while yt-dlp still reports every impersonate
    target as unavailable, because a different Python is running.

    `python -m yt_dlp` removes the ambiguity: same interpreter, same
    site-packages, same curl_cffi. Falls back to the PATH executable when
    the module is not installed here.
    """
    try:
        import yt_dlp  # noqa: F401
    except Exception:
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


# Resolved once - it cannot change while the process runs, and every
# argument builder below starts with it.
YTDLP = ytdlp_command()


PLATFORM_YOUTUBE = "youtube"
PLATFORM_TWITCH = "twitch"
PLATFORM_KICK = "kick"


def platform_of(url: str) -> str:
    """Which site this URL is on. Decides which flags are legal.

    Not cosmetic: --live-from-start is a YouTube-only capability (it walks
    back through the DASH manifest's sequence numbers). Neither Twitch nor
    Kick has an equivalent, and passing it there produces a warning and no
    benefit.
    """
    lowered = (url or "").lower()
    if "twitch.tv" in lowered:
        return PLATFORM_TWITCH
    if "kick.com" in lowered:
        return PLATFORM_KICK
    return PLATFORM_YOUTUBE


def is_clips_url(url: str) -> bool:
    """True for a clips listing rather than a single stream.

    A clips page is a playlist of finished videos, so it is downloaded
    once rather than recorded live - and re-running must not fetch the
    same clips again.
    """
    return "/clips" in (url or "").lower()


# yt-dlp's --wait-for-video chatter. Six lines a minute, forever, between
# streams: the URL, the webpage fetch, "not currently live", and three
# countdown lines. Over a night that is thousands of lines, and it buries
# the one message that matters - the recording actually starting.
_WAITING_NOISE = (
    "[wait]",
    "Downloading webpage",
    "Extracting URL",
    "is not currently live",
    "Downloading API JSON",
    "Re-extracting data",
)


def is_waiting_noise(line: str) -> bool:
    """True for a line that only says 'still not live'."""
    return any(marker in line for marker in _WAITING_NOISE)


# What actually starting to record looks like. Recognising the START is
# far more reliable than enumerating every extractor's chatter: listing
# the noise meant Twitch's "[twitch:stream] Downloading stream GraphQL"
# was read as real output, so the console flipped between "Not live yet"
# and "Live - recording started" every few seconds while nothing had
# changed.
_RECORDING_MARKERS = (
    "[download] Destination:",
    "[download] Resuming",
    "[hlsnative]",
    "[Merger]",
    "[FixupM3u8]",
    "Downloading item",
)


# A run of these with no progress between them means the manifest is
# dead rather than the network being flaky.
MAX_FRAGMENT_REFUSALS = 40

# A fresh manifest fixes a STALE one. It cannot fix a yt-dlp that YouTube
# no longer speaks to, and that failure looks identical from here: every
# fragment 403s, the run is abandoned, the next one starts clean and every
# fragment 403s again. A whole stream was lost to this - hours of
#
#   [download] Got error: HTTP Error 403: Forbidden. Retrying fragment 362
#
# and then "Channel is not live (or the recording never started)".
#
# Two restarts that never downloaded a single fragment between them is the
# signal. One restart can legitimately be a stale manifest; two in a row
# with no progress at all is something the restart cannot reach.
REFUSAL_RESTARTS_BEFORE_UPDATE = 2


def update_yt_dlp(runner=None) -> tuple:
    """(updated, detail). Bring yt-dlp up to date, once.

    YouTube changes its player and its manifests constantly and yt-dlp
    follows within days, so "every fragment is refused" is far more often
    an out-of-date yt-dlp than anything about the stream. This is the
    single maintenance task a recorder like this needs, and it is not
    reasonable to expect somebody to know that at 3am while a stream they
    wanted is going out unrecorded.
    """
    import subprocess

    runner = runner or subprocess.run
    command = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
    try:
        done = runner(command, capture_output=True, text=True, timeout=300)
    except Exception as exc:
        return False, str(exc)
    if getattr(done, "returncode", 1) != 0:
        detail = (getattr(done, "stderr", "") or "").strip().splitlines()
        return False, (detail[-1] if detail else "pip failed")
    out = (getattr(done, "stdout", "") or "")
    if "Successfully installed" in out:
        version = ""
        for word in out.split():
            if word.startswith("yt-dlp-"):
                version = word[len("yt-dlp-"):]
        return True, f"updated to {version}" if version else "updated"
    return True, "already the latest version"

# Logs are kept because "it just stopped" is unanswerable without them -
# but they are kept FOREVER, one per poll, and a recorder that polls
# every 60 seconds for days produces thousands. A real folder had 17,311
# files and 6 GB in it, one log 33 MB of the same 403 repeated. Keep the
# recent ones, which are the only ones anybody reads.
KEEP_LOGS = 60

# A single log past this is a loop writing to disk, not a record of a
# recording. Truncated rather than deleted: the head says how it began.
MAX_LOG_BYTES = 5 * 1024 * 1024

# The title is asked for the moment recording starts, and on a stream
# that has just gone live that call often comes back empty - the platform
# has not published it yet. It was asked ONCE, so an empty answer meant
# the stream kept its timestamp filename forever and was published as
# "Gaming Stream" while the streamer had called it "Copyrighting All Yall
# Plug Channels". Asked again, while it is still live, until it answers.
TITLE_RETRY_SECONDS = 120
MAX_TITLE_TRIES = 15


def remember_title(log_path: str, title: str) -> str:
    """Write the title beside the recording, as a .txt the uploader reads.

    The filename cannot carry it - it is built before the title is known,
    and --restrict-filenames would flatten it anyway - so the title lives
    in a sidecar. get_stream_title() already looks for exactly this file,
    which is why a stream recorded under a timestamp name can still be
    published under the name the streamer gave it.
    """
    if not log_path or not title:
        return ""
    sidecar = os.path.splitext(log_path)[0] + ".txt"
    try:
        with open(sidecar, "w", encoding="utf-8") as handle:
            handle.write(title.strip() + "\n")
    except OSError:
        return ""
    return sidecar


def source_sidecar(video_path: str) -> str:
    """Where a recording notes the stream it came from."""
    return os.path.splitext(video_path or "")[0] + ".source.txt"


def remember_source(video_path: str, url: str) -> str:
    """Write down which stream this recording is, beside it.

    The clip picker can read a stream's CHAT - messages per second,
    counted and thrown away - and chat is the audience saying out loud
    what was funny. It is the best signal available and better than
    anything measured from the audio: a hundred people typing at once is
    a much better reason to clip something than a loud noise is.

    It has never once run. The chat step needs a URL, and a recording is
    a local file, so every clip this project has ever made was picked
    from transcript shape and loudness while the audience's own verdict
    sat unread.

    One line, written once, is all that was missing.
    """
    if not video_path or not str(url or "").strip():
        return ""
    path = source_sidecar(video_path)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(url).strip() + "\n")
    except OSError:
        return ""
    return path


def prune_logs(folder: str, keep: int = KEEP_LOGS) -> int:
    """Delete all but the newest `keep` .log files. Returns how many went.

    Cannot raise. Four recorders run at once - youtube, twitch, kick and
    a second channel - and every one of them prunes this same folder. The
    mtime used to be read inside a sort key, so a file deleted by one of
    them between another's listdir and its getmtime raised
    FileNotFoundError where nothing caught it, and the whole recorder
    exited on the traceback before it had watched anything:

        logs.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        FileNotFoundError: [WinError 2] The system cannot find the file
        specified: '...\\Stackswopo youtube live 2026-08-17 16_31.log'

    Tidying up old logs must never be able to stop a recording. Times are
    read in one pass now, and a file that has gone is simply not in the
    list - which is the correct answer, because somebody else already
    deleted it.
    """
    try:
        names = os.listdir(folder)
    except OSError:
        return 0

    dated = []
    for name in names:
        if not name.endswith(".log"):
            continue
        path = os.path.join(folder, name)
        try:
            dated.append((os.path.getmtime(path), path))
        except OSError:
            continue

    if len(dated) <= keep:
        return 0
    dated.sort(reverse=True)
    removed = 0
    for _when, path in dated[keep:]:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            # Another recorder got there first, or Windows has it open.
            pass
    return removed

_REFUSAL = re.compile(r"HTTP Error (?:403|401|410)\b.*Retrying fragment",
                      re.IGNORECASE)


def is_fragment_refusal(line: str) -> bool:
    """A fragment the server will not serve, however many times we ask."""
    return bool(_REFUSAL.search(line or ""))


def is_progress_line(line: str) -> bool:
    """Evidence the download is actually moving."""
    text = (line or "").strip()
    return ("[download]" in text and "Retrying" not in text
            and ("%" in text or "Destination" in text
                 or "has already been" in text))


# yt-dlp reached a live stream and came away with nothing. Distinct from
# "this channel is not live", which is the normal state and says so on
# every poll.
_MISSED_MARKERS = (
    ("did not get any data blocks", "it produced no data"),
    ("video is no longer live", "it had already ended"),
    ("this live event has ended", "it had already ended"),
    ("the livestream has ended", "it had already ended"),
)


def _missed_stream(tail) -> str:
    """Why a found stream produced nothing, or "" if none was found.

    Only fires when yt-dlp got far enough to be TALKING about a live
    video. A channel that is simply offline never reaches these lines.
    """
    text = " ".join(tail or ()).lower()
    for marker, why in _MISSED_MARKERS:
        if marker in text:
            return why
    return ""


def is_recording_line(line: str) -> bool:
    """True once bytes are actually being fetched."""
    if any(marker in line for marker in _RECORDING_MARKERS):
        return True
    # "[download]  12.3% of ~4.20GiB" - progress, but not the bare
    # "[download] Downloading playlist" announcements.
    return "[download]" in line and "%" in line


# Failures that have a specific, known fix. Printed instead of leaving
# the raw error to be searched for.
_CURL_CFFI_FIX = (
    "Kick sits behind Cloudflare, and yt-dlp needs a browser TLS "
    "fingerprint to get past it. THE VERSION MATTERS - 0.16 installs and "
    "imports fine while yt-dlp reports every impersonate target as "
    "unavailable.\n"
    "    Do NOT pin a curl_cffi version by hand. This advice used to name "
    "one, and it went stale: yt-dlp moved on, and the pin then DOWNGRADED "
    "below what yt-dlp is built against - breaking the very thing it was "
    "meant to protect. Let yt-dlp pick the version it was built for:\n"
    "        python -m pip install -U \"yt-dlp[default,curl-cffi]\"\n"
    "    If curl_cffi is ALREADY installed and this still fails, the yt-dlp "
    "being run is the standalone .exe, which bundles its own Python and "
    "cannot see it. Check with:\n"
    "        python -m yt_dlp --list-impersonate-targets\n"
    "    Targets listed there but not by plain `yt-dlp` means exactly that; "
    "installing yt-dlp with pip fixes it, and this recorder then uses it "
    "automatically.")

# Ordered most specific first: a Kick 403 and a generic mid-recording 403
# have completely different fixes, and the generic one matching first
# would send you looking in the wrong place.
_CLOCK_FIX = (
    "This PC's clock is wrong. \"Certificate is not yet valid\" means the "
    "clock is BEHIND the date the site's certificate was issued, so every "
    "HTTPS connection fails - this will break YouTube, Rumble and Meta too, "
    "not just Kick. Nothing in this project can work around it.\n"
    "        Right-click the clock -> Adjust date and time -> turn \"Set "
    "time automatically\" off and back on -> Sync now.\n"
    "    Check the time zone while you are there.")

# The machine is offline, or DNS is. Nothing here is a site problem, and
# nothing is installable - so this must be matched BEFORE the Kick rule,
# which otherwise claims a dropped internet connection is Cloudflare and
# tells you to install a package.
#
# That is exactly what it did through a real outage: every DNS failure on
# a kick.com URL printed eight lines of curl_cffi installation advice.
_OFFLINE_FIX = (
    "This machine could not look up the address at all - that is the "
    "internet connection or DNS, not the site and not anything installed "
    "here. Nothing to fix in this project: the recorder keeps retrying and "
    "picks the stream back up on its own when the connection returns.")

_OFFLINE_MARKERS = (
    "could not resolve host",
    "failed to resolve",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "name or service not known",
    "[errno 11001]",
    "no address associated with hostname",
)

KNOWN_FIXES = tuple((marker, _OFFLINE_FIX) for marker in _OFFLINE_MARKERS) + (
    ("no impersonate target is available", _CURL_CFFI_FIX),
    # Before the Kick rule: this arrives on a Kick URL but has nothing to
    # do with Cloudflare, and the curl_cffi advice sends you to install a
    # package that can never fix a clock.
    ("certificate is not yet valid", _CLOCK_FIX),
    ("certificate has expired", _CLOCK_FIX),
    ("certificate verify failed", _CLOCK_FIX),
    ("kick", _CURL_CFFI_FIX),
    ("HTTP Error 403",
     "A 403 mid-recording usually means the fragment URLs expired. If this "
     "keeps happening on one platform, say so - it is fixable per site."),
)


def known_fix(line: str) -> str:
    """The fix for this error, if it is one with a known fix.

    The impersonation warning is unambiguous on its own and is matched
    anywhere - yt-dlp prints it as a WARNING, not an ERROR, so requiring
    an error would skip the one line that names the missing dependency.
    Everything else has to look like a failure first: "kick" appears in
    every ordinary Kick progress line too, and matching those would
    attach installation advice to a working recording.
    """
    lowered = line.lower()
    # First, because being offline explains every other error on the line
    # and none of them are worth acting on until it is back.
    for marker in _OFFLINE_MARKERS:
        if marker in lowered:
            return _OFFLINE_FIX
    if "no impersonate target is available" in lowered:
        return _CURL_CFFI_FIX
    # A TLS date failure is not a site problem and is worth naming even
    # when the line does not read as an error.
    for marker in ("certificate is not yet valid", "certificate has expired"):
        if marker in lowered:
            return _CLOCK_FIX
    if not is_worth_saying(line):
        return ""
    for marker, advice in KNOWN_FIXES:
        if marker.lower() in lowered:
            return advice
    return ""


def is_worth_saying(line: str) -> bool:
    """Errors always reach the console, even mid-wait.

    A wait that is quietly failing - an unavailable channel, a network
    that is down - must not look identical to a wait that is working.
    """
    stripped = line.lstrip()
    return stripped.startswith("ERROR") or "HTTP Error" in line


def _remove(path: str) -> None:
    """Delete a file if it is there. Never raises - every caller is
    cleaning up after itself and would rather leave a stray file than
    lose the recording to an exception on the tidy-up path."""
    try:
        os.remove(path)
    except OSError:
        pass


# How much shorter than its inputs a joined file may be before the join is
# treated as having lost something. Each seam costs a fraction of a second,
# so an exact match is not the test; two seconds a segment is.
JOIN_TOLERANCE_S = 2.0

# How long before the same problem is worth repeating. Long enough that a
# stuck channel does not fill the window; short enough that a problem
# still present in an hour says so again.
REPEAT_AFTER_S = 30 * 60


def join_lost_material(joined: Optional[float], parts: list,
                       tolerance_s: float = JOIN_TOLERANCE_S) -> bool:
    """True when the joined file is materially shorter than its parts.

    ffmpeg's concat demuxer does not always fail loudly. Given a segment
    whose tail is corrupt - which is exactly what a recording killed
    mid-fragment leaves - it can copy what it can, exit 0, and produce a
    short file. The segments would then be deleted as redundant when they
    are in fact the only complete copy.

    Durations that cannot be measured return False: refusing to delete on
    a failed probe would leak segments forever, and an unmeasurable file
    is not evidence of loss.
    """
    known = [d for d in parts if d]
    if not joined or len(known) != len(parts) or not known:
        return False
    return joined < (sum(known) - tolerance_s * len(known))


def vod_args(url: str, output_path: str, concurrent: int = 8) -> list:
    """Download the finished VOD rather than the live stream.

    Starting a recorder late costs the beginning of the stream:
    --live-from-start pulls what it can from YouTube's DVR buffer, but
    that buffer is finite - typically a few hours - so anything older than
    it is unrecoverable while the stream is running.

    Once the stream ends the whole thing becomes an ordinary video, and an
    ordinary video can be downloaded completely. No live flags, no waiting,
    and fragments can be fetched in parallel because there is no realtime
    pace to keep up with.
    """
    return YTDLP + [
        "--fragment-retries", "infinite",
        "--retries", "infinite",
        "--socket-timeout", "30",
        "--concurrent-fragments", str(concurrent),
        "--merge-output-format", "mp4",
        "--no-progress", "--newline",
        "-o", output_path,
        url,
    ]


def coverage_report(recorded: Optional[float],
                    expected: Optional[float]) -> str:
    """One line on whether the whole stream was captured.

    The point is to find out tonight rather than days later. A recording
    that stops at three hours of a five-hour stream is currently only
    noticed by watching it, and by then the stream is out of YouTube's
    DVR window and the missing part is gone for good.
    """
    if not recorded:
        return "could not measure the recording"
    if not expected:
        return f"recorded {recorded / 3600:.2f}h (stream length unknown)"

    ratio = recorded / expected
    if ratio >= COVERAGE_OK:
        return f"recorded {recorded / 3600:.2f}h of {expected / 3600:.2f}h - complete"
    missing = expected - recorded
    return (f"SHORT: recorded {recorded / 3600:.2f}h of {expected / 3600:.2f}h "
            f"- {missing / 60:.0f} min missing ({ratio:.0%}). Check the .log "
            "beside the recording for what stopped it.")


def safe_name(text: str, limit: int = 120) -> str:
    """A filename Windows will accept, keeping the title readable."""
    cleaned = "".join(c if c.isalnum() or c in SAFE_CHARS else "_"
                      for c in (text or "").strip())
    cleaned = " ".join(cleaned.split())
    return (cleaned[:limit].rstrip(" ._") or "stream")


def segment_path(staging: str, base: str, index: int) -> str:
    """Where segment N of one recording goes.

    Numbered rather than timestamped so ordering is lexical as well as
    numeric - concatenating them in the wrong order would be silent and
    disastrous.
    """
    return os.path.join(staging, f"{base}.part{index:02d}.ts")


# What a finished segment can be called. `-o "...part01.ts"` does NOT
# force the extension: yt-dlp treats ".ts" as part of the name, picks its
# own container when it merges video and audio, and appends that - so the
# finished file is "...part01.ts.mp4". Matching on a .ts suffix therefore
# missed the completed recording sitting right next to the fragments.
MEDIA_EXTENSIONS = (".ts", ".mp4", ".mkv", ".webm", ".mov", ".flv")

# Downloads still in flight. yt-dlp appends .part while writing and
# "-FragNNNNN.part" for the fragment it is on; either means unfinished.
_UNFINISHED = (".part", ".ytdl", ".tmp")


def is_unfinished(name: str) -> bool:
    """True while yt-dlp is still writing this file."""
    lowered = name.lower()
    return lowered.endswith(_UNFINISHED) or ".part-frag" in lowered


def existing_segments(staging: str, base: str) -> list:
    """Every finished segment for this recording, in order.

    Matched on the prefix rather than one expected extension, because
    which extension a finished segment ends up with is yt-dlp's decision,
    not ours.
    """
    if not os.path.isdir(staging):
        return []
    prefix = f"{base}.part"
    found = [
        os.path.join(staging, name) for name in sorted(os.listdir(staging))
        if name.startswith(prefix)
        and name.lower().endswith(MEDIA_EXTENSIONS)
        and not is_format_fragment(name)
        and not is_unfinished(name)
    ]
    return [p for p in found if os.path.getsize(p) > 0]


# yt-dlp downloads video and audio as separate streams and merges them
# afterwards, naming the halves "name.ts.f299" / "name.ts.f140" while it
# works. Those are the recording - they just have not been joined yet.
_FRAGMENT = re.compile(r"\.f\d+$")


def is_format_fragment(name: str) -> bool:
    """True for yt-dlp's pre-merge video-only/audio-only half."""
    return bool(_FRAGMENT.search(name))


def leftover_fragments(staging: str, base: str) -> list:
    """Unmerged halves left behind when yt-dlp died before merging.

    Finding these matters more than it sounds: they hold the entire
    recording, so reporting "nothing was recorded" because the merge did
    not run would throw away hours of stream that is sitting right there.
    """
    if not os.path.isdir(staging):
        return []
    return sorted(
        os.path.join(staging, name) for name in os.listdir(staging)
        if name.startswith(base) and is_format_fragment(name)
        and os.path.getsize(os.path.join(staging, name)) > 0)


def abandoned_part_files(staging: str, base: str) -> list:
    """A segment yt-dlp was still writing when its process stopped.

    yt-dlp writes to "<name>.part" while downloading and renames it to
    "<name>" ONLY on a clean finish - a finish this recorder never asks
    for. Every segment here is stopped on purpose: a stale manifest
    (process.terminate()), the outer loop giving up on a resume, Ctrl+C,
    the keepalive loop restarting the whole recorder, a crash, a power
    cut. None of those is a clean finish, so the rename never runs, and
    existing_segments()/leftover_fragments() were never taught to look
    for a finished recording still wearing yt-dlp's own ".part" name -
    they correctly treat ".part" as "still being written" and skip it,
    which is right for a file that IS still being written and wrong for
    one whose writer has already exited.

    --hls-use-mpegts is the reason this is safe to recover at all: every
    byte written so far is a valid, playable .ts. The content was never
    in danger - only its filename was one rename short of being found.

    Only ever called after the writer's process has exited
    (process.wait() has returned, or the process is gone entirely because
    this is a sweep on startup) - so nothing is still appending to these
    files and renaming them is safe.
    """
    if not os.path.isdir(staging):
        return []
    prefix = f"{base}.part"
    return sorted(
        os.path.join(staging, name) for name in os.listdir(staging)
        if name.startswith(prefix) and name.lower().endswith(".part")
        and os.path.getsize(os.path.join(staging, name)) > 0)


def recover_abandoned_parts(staging: str, base: str) -> list:
    """Rename yt-dlp's still-.part-suffixed segments back to finished ones.

    Renamed, not copied - a segment can be gigabytes, and a rename is
    instant where a copy is not. os.replace is atomic on both Windows and
    POSIX, so this can never leave a half-renamed file behind.
    """
    recovered = []
    for path in abandoned_part_files(staging, base):
        finished = path[: -len(".part")]
        try:
            os.replace(path, finished)
        except OSError:
            continue
        recovered.append(finished)
    return recovered


# Our own segment name is "<recorder name> <date> <time>.partNN.<ext>". A
# recording orphaned by a crash still wears that full shape, plus yt-dlp's
# trailing ".part" - this is what turns "some leftover file" back into
# "the base a whole earlier session was recording under".
_ABANDONED_BASE = re.compile(r"^(.*)\.part\d+\.[A-Za-z0-9]+$")


def sweep_abandoned_recordings(staging: str, name: str) -> list:
    """Every earlier recording under THIS name that never got finalised.

    Not just a process that this run itself terminated - one that never
    came back at all: the recorder was killed, lost power, or the
    keepalive loop restarted it mid-recording, all of which skip
    finalise() entirely and leave the recording exactly where
    abandoned_part_files describes. Matched on the recorder's own name
    prefix so one platform's sweep can never pick up another's files, and
    a date-and-time-stamped base can never collide with the fresh one
    this call is about to create.
    """
    if not os.path.isdir(staging):
        return []
    prefix = f"{safe_name(name)} "
    bases = set()
    for filename in os.listdir(staging):
        if not filename.startswith(prefix) or not filename.lower().endswith(".part"):
            continue
        if os.path.getsize(os.path.join(staging, filename)) <= 0:
            continue
        match = _ABANDONED_BASE.match(filename[: -len(".part")])
        if match:
            bases.add(match.group(1))
    return sorted(bases)


def build_concat_list(segments: list, list_path: str) -> str:
    """ffmpeg's concat demuxer input file.

    Paths are single-quoted with embedded quotes escaped, which is the
    format's own escaping rule - a Windows path with an apostrophe in the
    stream title would otherwise break the parse.
    """
    with open(list_path, "w", encoding="utf-8") as f:
        for path in segments:
            escaped = os.path.abspath(path).replace("\\", "/").replace("'", r"'\''")
            f.write(f"file '{escaped}'\n")
    return list_path


def should_resume(started_at: float, ended_at: float, resumes: int,
                  max_resumes: int = MAX_RESUMES) -> bool:
    """Was that a network blip mid-stream, or the stream actually ending?

    A recording that ran for a while and then stopped is the interesting
    case: the stream is probably still live and worth reconnecting to. One
    that ended almost immediately is a channel that is simply offline, and
    retrying in a tight loop would hammer it.
    """
    if resumes >= max_resumes:
        return False
    return (ended_at - started_at) > RESUME_WINDOW_S


@dataclass
class Recorder:
    url: str
    staging: str
    watch_folder: str
    name: str = "stream"
    poll_seconds: int = 60
    concurrent_fragments: int = 4
    # When the live recording came up short, download the finished VOD
    # instead. Off for a stream that is not archived afterwards.
    fill_gaps: bool = True
    # Captured when the recording starts, because a live URL resolves to
    # nothing once the stream has ended.
    title: str = ""

    # Why the last attempt came away with nothing, when a stream WAS
    # found. Read by the watch loop, which otherwise cannot tell that
    # from a channel being offline - see _missed_stream.
    last_missed: str = ""

    # How many attempts in a row have been abandoned with every fragment
    # refused and nothing downloaded. Reset by any real progress. See
    # REFUSAL_RESTARTS_BEFORE_UPDATE - two of these is not a stale
    # manifest, it is something a fresh manifest cannot reach.
    refusal_restarts: int = 0
    _tried_update: bool = False

    # What has already been said, and when. Kept on the recorder rather
    # than inside one attempt because the attempt is what repeats: a
    # channel that is not live restarts yt-dlp every poll, and a problem
    # that survives the restart was printing its whole explanation every
    # sixty seconds. Ten hours of that buries everything else.
    _said: dict = field(default_factory=dict, repr=False)
    _swept_orphans: bool = field(default=False, repr=False)

    def say(self, message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def say_once_for_everyone(self, key: str, message: str) -> bool:
        """Say this once for the whole run, not once per channel.

        Five channels are watched, each on its own thread, and each was
        printing the same sentence at the same second:

            Not live yet - checking every 60s. This window can stay open
            for days; each check is one page fetch, so it costs
            practically no data.       x4
            Holding the machine awake for the recording.   x4

        The same paragraph five times is not five facts. It made an idle
        recorder look like something going wrong, and it is the reason
        this window reads as busy when it is doing nothing at all.
        """
        with _EVERYONE_LOCK:
            if key in _SAID_FOR_EVERYONE:
                return False
            _SAID_FOR_EVERYONE.add(key)
        self.say(message)
        return True

    def say_once(self, key: str, message: str,
                 every_seconds: float = REPEAT_AFTER_S) -> bool:
        """Say this only if it has not been said recently. True if said.

        A problem that persists is still worth a reminder eventually -
        silence forever would look like it had cleared - so the same
        message comes back every half hour rather than every minute.
        """
        now = time.time()
        if now - self._said.get(key, 0.0) < every_seconds:
            return False
        self._said[key] = now
        self.say(message)
        return True

    # ── The yt-dlp invocation ────────────────────────────────────────────

    def download_args(self, output_path: str, wait: bool = True) -> list:
        args = YTDLP + [
            # Never stop on a fragment; this is the setting that keeps a
            # four-hour recording alive on a domestic connection.
            "--fragment-retries", "infinite",
            "--retries", "infinite",
            "--file-access-retries", "10",
            "--retry-sleep", "linear=1::5",
            "--socket-timeout", "30",
            # Valid at every byte, so an interrupted file still plays.
            "--hls-use-mpegts",
            "--no-playlist",
            "--concurrent-fragments", str(self.concurrent_fragments),
            "--no-progress",
            "--newline",
            "-o", output_path,
        ]
        if platform_of(self.url) == PLATFORM_YOUTUBE:
            # YouTube only. It walks back through the DASH manifest's
            # sequence numbers to pull what is still in the DVR buffer, so
            # starting late does not automatically cost the beginning.
            # Twitch has no equivalent and warns if it is passed.
            args.append("--live-from-start")
        if wait:
            args += ["--wait-for-video", str(self.poll_seconds)]
        args.append(self.url)
        return args

    def _run(self, args: list, log_path: str = "",
             quiet_wait: bool = True) -> int:
        """Run yt-dlp, echoing its output and keeping a copy on disk.

        The copy is the point. A recording that stops after three hours of
        a five-hour stream leaves nothing to look at once the window is
        closed, and "it just stopped" is not something anyone can fix. The
        last few lines of this log name the reason.
        """
        try:
            process = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, text=True, errors="replace")
        except FileNotFoundError:
            self.say("ERROR: yt-dlp is not on PATH. Install it: pip install -U yt-dlp")
            return 127

        tail: list = []
        log = None
        try:
            if log_path:
                folder = os.path.dirname(os.path.abspath(log_path))
                os.makedirs(folder, exist_ok=True)
                gone = prune_logs(folder)
                if gone:
                    self.say(f"Cleared {gone} old log file(s).")
                # A log that has already run away is not worth appending
                # to - it is the same line a hundred thousand times.
                try:
                    if os.path.getsize(log_path) > MAX_LOG_BYTES:
                        os.remove(log_path)
                except OSError:
                    pass
                log = open(log_path, "a", encoding="utf-8")
                log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                          f"{' '.join(args)}\n")
            # Suppression is opt-in per call: a clips download has no
            # "waiting for live" phase, and running it through this state
            # machine would report it as a stream starting.
            waiting = quiet_wait
            waiting_since = last_heartbeat = 0.0
            # Said once per run, not once per poll - this loop retries
            # every 60 seconds and would otherwise repeat the advice all
            # night.
            said_fixes: set = set()
            # A 403 on a fragment is NOT a transient network error, and
            # retrying it forever is why a recorder sat spinning on
            # fragment 97 a hundred and eighty thousand times. It means
            # the segment URL is dead - an expired token, a rotated CDN
            # path, a stream that ended and took its manifest with it -
            # and no number of retries can bring that fragment back. The
            # recovery is a FRESH manifest, which is exactly what the
            # outer loop does when this process exits, and which infinite
            # retries prevent it from ever reaching.
            #
            # So: infinite retries stay, because they are right for a
            # dropped connection. A RUN of refusals with no progress
            # between them ends the process instead.
            refusals = 0
            # Did this attempt ever actually download anything? A restart
            # after real progress is a stale manifest doing what stale
            # manifests do. A restart after NOTHING is a different
            # problem, and the difference is the whole diagnosis.
            downloaded_anything = False
            title_tries = 0
            title_next_try = 0.0
            for line in process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-40]

                # Still live and still nameless: ask again. A title that
                # arrives ten minutes in is worth just as much as one
                # that arrived at second zero, and the alternative is a
                # stream published under a placeholder.
                if (not waiting and not self.title and title_tries < MAX_TITLE_TRIES
                        and time.time() >= title_next_try):
                    title_tries += 1
                    title_next_try = time.time() + TITLE_RETRY_SECONDS
                    self.title = stream_title(self.url)
                    if self.title:
                        self.say(f'Title: "{self.title}"')
                        remember_title(log_path, self.title)

                if is_fragment_refusal(line):
                    refusals += 1
                    if refusals >= MAX_FRAGMENT_REFUSALS:
                        if downloaded_anything:
                            self.say(f"The stream's segments are being "
                                     f"refused ({refusals} in a row) - the "
                                     f"manifest has gone stale. Restarting "
                                     f"with a fresh one.")
                            self.refusal_restarts = 0
                        else:
                            self.refusal_restarts += 1
                            self.say(f"Every segment refused ({refusals} in "
                                     f"a row, nothing downloaded). "
                                     f"Restarting.")
                            self._fix_refusals()
                        process.terminate()
                        break
                elif is_progress_line(line):
                    # Real progress clears the count: a handful of 403s
                    # scattered through a long recording is normal and
                    # must not end it.
                    refusals = 0
                    downloaded_anything = True
                if log:
                    # The log keeps EVERYTHING. Quietening the console is
                    # about being able to read it; throwing away the
                    # record of what happened is a different thing, and
                    # the log is the only evidence when a recording
                    # stops.
                    log.write(line + "\n")
                    log.flush()

                if waiting:
                    # Only the download actually starting ends the wait.
                    # Deciding by "not a known noise line" instead meant
                    # every extractor's own chatter looked like the
                    # stream beginning.
                    if is_recording_line(line):
                        waiting = False
                        self.say("Live - recording started.")
                        # Asked NOW, while the stream is still live - a
                        # live URL resolves to nothing once it ends.
                        if not self.title:
                            self.title = stream_title(self.url)
                            if self.title:
                                self.say(f'Title: "{self.title}"')
                                remember_title(log_path, self.title)
                            else:
                                title_next_try = time.time() + TITLE_RETRY_SECONDS
                    elif is_worth_saying(line):
                        # Deduplicated on the error itself, not the whole
                        # line: yt-dlp restates the same failure with a
                        # different URL fragment every attempt.
                        advice = known_fix(line)
                        key = advice or " ".join(line.split()[:12])
                        if self.say_once(key, line.strip()) and advice:
                            self.say(f"FIX: {advice}")
                        continue
                    else:
                        now = time.time()
                        if not waiting_since:
                            waiting_since = last_heartbeat = now
                            self.say_once_for_everyone(
                                "not-live-yet",
                                f"Nothing live. Checking every "
                                f"{self.poll_seconds}s from now on, quietly - "
                                f"nothing more prints until a stream starts. "
                                f"On YouTube the recording still begins at the "
                                f"stream's first second, so the check interval "
                                f"costs no footage.")
                        elif now - last_heartbeat >= WAIT_HEARTBEAT_S:
                            # Per channel would be five identical lines
                            # every half hour, all night.
                            if self.say_once(
                                    "still-waiting",
                                    f"Still watching "
                                    f"({(now - waiting_since) / 3600:.1f}h). "
                                    f"Full detail: {log_path or 'the log'}",
                                    every_seconds=WAIT_HEARTBEAT_S):
                                pass
                            last_heartbeat = now
                        continue

                print(line, flush=True)
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            self.say("Stopped by Ctrl+C.")
            return 130
        finally:
            if log:
                log.close()
            # A stream that was FOUND and then produced nothing is not
            # the same as a channel that was never live, and the two
            # printed the same thing: "Still waiting". So a genuinely
            # missed stream - yt-dlp connecting, being told the video is
            # no longer live, and exiting with no data - scrolled past as
            # ordinary polling chatter, and the only way to know was to
            # go and read the log.
            missed = _missed_stream(tail)
            self.last_missed = missed
            if missed:
                self.say(f"MISSED a stream at "
                         f"{time.strftime('%H:%M:%S')} - {missed}. "
                         f"Nothing was saved. Still watching.")
            if tail and process.returncode not in (0, None):
                # Same dedup: a channel that is not live fails this way
                # every poll, and the tail is identical every time.
                signature = " ".join(" ".join(tail[-3:]).split()[:14])
                if self.say_once(f"tail:{signature}",
                                 "Last thing yt-dlp said before stopping:"):
                    for line in tail[-6:]:
                        self.say(f"    {line}")
                # The line naming the missing dependency appears HERE and
                # nowhere else - yt-dlp prints it as a warning on its way
                # out, after the error that actually stopped it. Checking
                # only the live stream meant the useful advice was the
                # one line never examined.
                for line in tail:
                    advice = known_fix(line)
                    if advice:
                        self.say_once(f"fix:{advice[:40]}", f"FIX: {advice}")
                        break

    def _fix_refusals(self) -> None:
        """Act on a run of attempts that never downloaded anything.

        Restarting is the right answer to a stale manifest and no answer
        at all to an out-of-date yt-dlp, and from here the two look
        identical: every fragment 403s, the attempt is abandoned, the next
        one starts clean and every fragment 403s again. A whole stream was
        lost to exactly that.

        YouTube changes its player constantly and yt-dlp follows within
        days, so an update is by far the likeliest fix - and it is not
        reasonable to expect anybody to know that at 3am while the stream
        they wanted goes out unrecorded. Once per run, never in a loop.
        """
        if self.refusal_restarts < REFUSAL_RESTARTS_BEFORE_UPDATE:
            return
        if self._tried_update:
            self.say_once(
                "refusals:updated",
                "Every segment is still being refused after updating "
                "yt-dlp. That is not a stale manifest and not a version "
                "problem - the stream may need cookies "
                "(--cookies-from-browser), or this platform is blocking "
                "this machine.")
            return

        self._tried_update = True
        self.say(f"{self.refusal_restarts} attempts in a row downloaded "
                 f"nothing at all. That is not a stale manifest - yt-dlp is "
                 f"most likely out of date. Updating it now...")
        updated, detail = update_yt_dlp()
        if updated:
            self.say(f"yt-dlp {detail}. Trying the stream again.")
        else:
            self.say(f"Could not update yt-dlp ({detail}). Run this by hand "
                     f"and restart the recorder:")
            self.say("    python -m pip install -U yt-dlp")

    # ── Assembling and delivering ────────────────────────────────────────

    def finalise(self, base: str) -> Optional[str]:
        """Join the segments and move the result into the watch folder."""
        recovered = recover_abandoned_parts(self.staging, base)
        if recovered:
            self.say(f"Recovered {len(recovered)} segment(s) yt-dlp never "
                     f"finished renaming after this recording was stopped - "
                     f"the content is intact, only the rename never ran.")
        segments = existing_segments(self.staging, base)
        if not segments:
            # The merge may simply not have run. The halves still hold the
            # whole stream, so recover them rather than declaring failure.
            recovered = self._merge_fragments(base)
            if recovered:
                segments = [recovered]
        if not segments:
            self.say("Nothing was recorded.")
            return None

        final = os.path.join(self.staging, f"{base}.mp4")
        if len(segments) == 1:
            ok = self._remux(segments[0], final)
        else:
            self.say(f"Joining {len(segments)} segments "
                     "(the recording was interrupted and resumed)...")
            ok = self._concat(segments, final)

        if not ok:
            # The .ts files still play. Losing the join is annoying; losing
            # the recording because the join failed would not be.
            self.say("Could not produce a single file - the .ts segments are "
                     f"still in {self.staging} and are playable as they are.")
            return None

        # Measured BEFORE the segments are gone, because this is the only
        # moment both the parts and the whole exist to compare.
        part_lengths = [probe_duration(p) for p in segments]

        destination = os.path.join(self.watch_folder, os.path.basename(final))
        os.makedirs(self.watch_folder, exist_ok=True)
        try:
            shutil.move(final, destination)
        except OSError as exc:
            self.say(f"Could not move the finished file: {exc}")
            return None

        remember_source(destination, self.url)

        if join_lost_material(probe_duration(destination), part_lengths):
            # Nothing is deleted on this path. The joined file is still
            # delivered - it is watchable and mostly right - but the
            # segments are the complete copy and are worth more than the
            # disk they occupy.
            self.say(f"The joined file is shorter than the segments it was "
                     f"built from, so the segments have been KEPT in "
                     f"{self.staging}. Nothing is lost - they hold the full "
                     "recording and can be re-joined by hand.")
        else:
            for path in segments:
                _remove(path)

        # Checked here, while the stream is still in YouTube's DVR window
        # and a missing hour could still be re-fetched. Days later it is
        # gone for good.
        report = coverage_report(probe_duration(destination),
                                 expected_duration(self.url))
        self.say(report)
        # Said out loud because a delay between the voice and the picture
        # is invisible in a duration and obvious to everyone watching.
        self.say(sync_report(destination))
        if report.startswith("SHORT") and self.fill_gaps:
            replaced = self._replace_with_vod(base, destination)
            if replaced:
                destination = replaced
                report = coverage_report(probe_duration(destination),
                                         expected_duration(self.url))
                self.say(report)
        if report.startswith("SHORT"):
            self._notify_short(report)

        # The real title, written where the uploader looks for it.
        if self.title:
            try:
                with open(os.path.splitext(destination)[0] + ".txt", "w",
                          encoding="utf-8") as f:
                    f.write(self.title + "\n")
                self.say(f'Title: "{self.title}"')
            except OSError:
                pass

        self.say(f"Delivered -> {destination}")
        self.say("The uploader will pick it up (run: python main.py --watch)")
        return destination

    def _replace_with_vod(self, base: str, current: str) -> Optional[str]:
        """Swap a short recording for the complete published VOD.

        Only worth doing when the VOD is genuinely longer - a stream that
        was not archived, or one whose VOD is itself partial, would
        otherwise trade a real recording for a worse one.
        """
        self.say("Recording is short - trying the published VOD, which has "
                 "the whole stream...")
        candidate = os.path.join(self.staging, f"{base}.vod.mp4")
        code = self._run(vod_args(self.url, candidate),
                         os.path.join(self.staging, f"{base}.log"))
        if code != 0 or not os.path.exists(candidate):
            self.say("The VOD is not available yet. YouTube can take a while "
                     "to publish one after a stream ends - re-run later if the "
                     "recording matters.")
            _remove(candidate)
            return None

        vod_length = probe_duration(candidate) or 0
        have_length = probe_duration(current) or 0
        if vod_length <= have_length:
            self.say(f"The VOD is no longer than what was recorded "
                     f"({vod_length / 3600:.2f}h vs {have_length / 3600:.2f}h) "
                     "- keeping the recording.")
            _remove(candidate)
            return None

        # The live recording is moved aside rather than overwritten. A
        # longer VOD is very probably a superset of it, but "probably" is
        # not good enough to destroy the only other copy with, so the
        # swap is made reversible and then only finalised below.
        destination = os.path.join(self.watch_folder, os.path.basename(current))
        kept = os.path.join(self.staging, f"{base}.live-recording.mp4")
        try:
            os.replace(current, kept)
            os.replace(candidate, destination)
        except OSError as exc:
            self.say(f"Could not put the VOD in place: {exc}")
            # Undo, so a failure here leaves the live recording delivered
            # exactly as it was rather than nothing at all.
            if not os.path.exists(destination) and os.path.exists(kept):
                try:
                    os.replace(kept, destination)
                except OSError:
                    self.say(f"The recording is safe at {kept}.")
            return None
        self.say(f"Replaced with the full VOD "
                 f"({vod_length / 3600:.2f}h, was {have_length / 3600:.2f}h).")

        expected = expected_duration(self.url)
        if expected and vod_length >= expected * COVERAGE_OK:
            # The VOD covers the whole stream, so the shorter live copy is
            # genuinely redundant - not merely superseded - and holding
            # gigabytes of it would fill the disk the next recording needs.
            _remove(kept)
        else:
            self.say(f"Keeping the live recording at {kept} as well - the VOD "
                     "is longer but not provably complete, so both copies stay "
                     "until you have checked. Delete it once you are happy.")
        return destination

    def _notify_short(self, report: str) -> None:
        """Ping Discord when a recording came up short, because nobody
        watches the console for five hours."""
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if not webhook:
            return
        import json
        import urllib.request

        payload = json.dumps({
            "content": f"⚠️ **Recording came up short** — {self.name}\n{report}"
        }).encode("utf-8")
        try:
            urllib.request.urlopen(urllib.request.Request(
                webhook, data=payload,
                headers={"Content-Type": "application/json",
                         "User-Agent": "AutoBleep"}), timeout=15)
        except Exception:
            pass

    def _merge_fragments(self, base: str) -> Optional[str]:
        """Join yt-dlp's leftover video-only and audio-only halves.

        yt-dlp normally does this itself; when it is killed mid-merge the
        halves are all that survive, and they contain the full recording.
        """
        fragments = leftover_fragments(self.staging, base)
        if not fragments:
            return None

        self.say(f"Found {len(fragments)} unmerged stream half/halves - "
                 "yt-dlp did not finish joining them. Recovering...")
        target = segment_path(self.staging, base, 1)
        args = []
        for path in fragments:
            args += ["-i", path]
        args += ["-c", "copy", target]

        if not self._ffmpeg(args):
            self.say("Could not join them automatically. They are still in "
                     f"{self.staging} and hold the full recording - join them "
                     "with: ffmpeg -i <video>.f<N> -i <audio>.f<N> -c copy out.mp4")
            return None

        for path in fragments:
            try:
                os.remove(path)
            except OSError:
                pass
        self.say("Recovered.")
        return target

    def _remux(self, source: str, target: str) -> bool:
        """TS -> MP4, video copied, audio put back on the video's clock.

        Video is never re-encoded, so this stays fast and lossless. The
        audio is, because that is the only way to fill the holes a live
        recording leaves - see _SYNC_AUDIO.
        """
        return self._ffmpeg(["-fflags", "+genpts", "-i", source]
                            + _SYNC_AUDIO + _SYNC_MUX
                            + ["-movflags", "+faststart", target])

    def _normalise(self, source: str, target: str) -> bool:
        """One segment, audio realigned, still MPEG-TS so it can be joined."""
        return self._ffmpeg(["-fflags", "+genpts", "-i", source]
                            + _SYNC_AUDIO + _SYNC_MUX
                            + ["-f", "mpegts", target])

    def _concat(self, segments: list, target: str) -> bool:
        """Join the segments into one file.

        Each segment is realigned BEFORE the join, not after. The concat
        demuxer splices audio end to end, so by the time the segments are
        one file the gaps are invisible and nothing can put them back -
        the drift has to be taken out while each piece still knows how
        long its own picture was.
        """
        joinable, temporary = [], []
        for index, segment in enumerate(segments):
            fixed = os.path.join(self.staging, f"_sync{index:03d}.ts")
            if self._normalise(segment, fixed):
                joinable.append(fixed)
                temporary.append(fixed)
            else:
                # Better a segment with drift than no segment at all.
                self.say(f"Could not realign {os.path.basename(segment)} - "
                         "joining it as it is.")
                joinable.append(segment)

        list_path = os.path.join(self.staging, "_concat.txt")
        build_concat_list(joinable, list_path)
        ok = self._ffmpeg(["-fflags", "+genpts",
                           "-f", "concat", "-safe", "0", "-i", list_path,
                           "-c", "copy"] + _SYNC_MUX
                          + ["-movflags", "+faststart", target])
        for path in temporary + [list_path]:
            _remove(path)
        return ok

    def _ffmpeg(self, args: list) -> bool:
        try:
            completed = subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            self.say("ERROR: ffmpeg is not on PATH.")
            return False
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            self.say(f"ffmpeg failed: {detail[-300:]}")
            return False
        return True

    # ── The loop ─────────────────────────────────────────────────────────

    def record_one_stream(self) -> Optional[str]:
        """Wait for the channel to go live, record it, deliver it."""
        os.makedirs(self.staging, exist_ok=True)

        if not self._swept_orphans:
            # Once per process, not once per poll: a recovery that keeps
            # failing (a full disk, a corrupt file) must not run ffmpeg
            # over a multi-gigabyte file again every poll interval
            # forever. The common case - recovering after a crash or a
            # restart - only ever needs to happen once anyway.
            self._swept_orphans = True
            for orphan_base in sweep_abandoned_recordings(self.staging, self.name):
                self.say(f"Found a recording that never finished from an "
                         f"earlier run ({orphan_base}) - recovering it "
                         f"before waiting for the next stream.")
                recovered_path = self.finalise(orphan_base)
                if recovered_path:
                    self.say(f"Recovered and delivered: "
                             f"{os.path.basename(recovered_path)}")

        base = f"{safe_name(self.name)} {time.strftime('%Y-%m-%d %H_%M')}"
        log_path = os.path.join(self.staging, f"{base}.log")

        warning = disk_warning(self.staging)
        if warning:
            self.say(f"WARNING: {warning}")

        self.say(f"Waiting for {self.name} to go live...")
        resumes = 0
        missed_tries = 0
        while True:
            target = segment_path(self.staging, base, resumes + 1)
            started = time.time()
            # Held only while actually downloading, so the machine can
            # still sleep normally during the wait between streams.
            with KeepAwake() as awake:
                if resumes == 0 and awake.active:
                    self.say_once_for_everyone(
                        "keep-awake",
                        "Holding the machine awake for the recording.")
                code = self._run(self.download_args(target, wait=(resumes == 0)),
                                 log_path)
            ended = time.time()

            if code in (127, 130):
                return None
            if code == 0:
                self.say("Stream ended.")
                break
            if not should_resume(started, ended, resumes):
                # A stream that was FOUND and produced nothing is not a
                # channel that is offline, and this treated them the
                # same: should_resume only reconnects an attempt that
                # ran longer than RESUME_WINDOW_S, and a 503 fails in
                # seconds. So a live stream behind a transient CDN error
                # was written off as "not live" on the first try, and
                # the next look came a whole poll later.
                #
                # 503s and dropped fragments clear. Retry a few times,
                # briefly - if the stream really has ended, this costs a
                # minute before going back to watching.
                if (resumes == 0 and self.last_missed
                        and missed_tries < MAX_MISSED_TRIES):
                    missed_tries += 1
                    self.say(f"Found a stream but got nothing "
                             f"({self.last_missed}) - trying again in "
                             f"{MISSED_RETRY_SECONDS}s "
                             f"({missed_tries}/{MAX_MISSED_TRIES}).")
                    time.sleep(MISSED_RETRY_SECONDS)
                    continue
                if resumes == 0:
                    self.say("Channel is not live (or the recording never started).")
                    return None
                break

            resumes += 1
            self.say(f"Recording dropped after "
                     f"{(ended - started) / 60:.0f} min - reconnecting "
                     f"(resume {resumes}/{MAX_RESUMES})...")
            self.say(f"Full yt-dlp output: {log_path}")
            time.sleep(3)

        return self.finalise(base)


def clips_args(url: str, output_path: str, archive_path: str,
               limit: int = 0) -> list:
    """Download clips from a clips listing, skipping ones already fetched.

    --download-archive is what makes this safe to re-run: yt-dlp records
    every downloaded id in that file and never fetches it twice. Without
    it, a second run would re-download every clip and hand the uploader a
    pile of duplicates.

    --playlist-end bounds a listing that could be hundreds of clips long.
    """
    args = YTDLP + [
        "--fragment-retries", "infinite",
        "--retries", "infinite",
        "--socket-timeout", "30",
        "--concurrent-fragments", "4",
        "--download-archive", archive_path,
        "--no-post-overwrites",
        # Clip titles are whatever the clipper typed, and on Twitch that
        # is routinely emoji. A filename containing them survives the
        # download and then breaks everything downstream on Windows -
        # shutil.move raised "[WinError 2] The system cannot find the
        # file specified" for a file plainly sitting there, because the
        # name that came back from listdir and the name the move used
        # were not encoded the same way. ASCII-only names cost a little
        # readability and remove the whole class of failure.
        "--restrict-filenames",
        "--merge-output-format", "mp4",
        "--no-progress", "--newline",
        "-o", output_path,
    ]
    if limit:
        args += ["--playlist-end", str(limit)]
    args.append(url)
    return args


def fetch_clips(url: str, staging: str, watch_folder: str,
                name: str = "clips", limit: int = 0) -> list:
    """Download any NEW clips from a clips page into the watch folder.

    Clips are already finished videos, so there is nothing to record -
    this is a download, and the only interesting part is not fetching the
    same ones twice.
    """
    os.makedirs(staging, exist_ok=True)
    os.makedirs(watch_folder, exist_ok=True)
    prefix = safe_name(name)
    archive = os.path.join(staging, f"{prefix}.clips-archive.txt")
    before = set(os.listdir(staging))

    template = os.path.join(staging, f"{prefix} %(title)s.%(ext)s")
    recorder = Recorder(url=url, staging=staging, watch_folder=watch_folder,
                        name=name)
    recorder.say(f"Checking {url} for new clips...")
    recorder._run(clips_args(url, template, archive, limit),
                  os.path.join(staging, f"{prefix}.log"),
                  quiet_wait=False)

    delivered = []
    for filename in sorted(set(os.listdir(staging)) - before):
        source = os.path.join(staging, filename)
        # Only this fetcher's own files. Live recorders share the staging
        # folder and write finished-looking .ts segments between the
        # download ending and the join starting - handing one of those to
        # the uploader would publish a fragment of a stream and delete it
        # out from under the recorder.
        if not filename.startswith(prefix):
            continue
        if is_unfinished(filename) or not filename.lower().endswith(MEDIA_EXTENSIONS):
            continue
        if os.path.getsize(source) == 0:
            continue
        try:
            shutil.move(source, os.path.join(watch_folder, filename))
        except OSError as exc:
            recorder.say(f"Could not deliver {filename}: {exc}")
            continue
        delivered.append(filename)

    if delivered:
        recorder.say(f"Delivered {len(delivered)} new clip(s) -> {watch_folder}")
    else:
        recorder.say("No new clips.")
    return delivered


def _source_loop(recorder: "Recorder", once: bool, stop) -> None:
    """Record one source forever. One of these runs per URL."""
    try:
        while not stop.is_set():
            recorder.record_one_stream()
            if once:
                return
            stop.wait(recorder.poll_seconds)
    except KeyboardInterrupt:
        pass


def main(argv: Optional[list] = None) -> int:
    import argparse
    import threading

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)

    parser = argparse.ArgumentParser(
        description="Record live streams into the uploader\'s watch folder. "
                    "Give more than one URL to watch several channels at "
                    "once - YouTube and Twitch together, same output folder.")
    parser.add_argument("url", nargs="+",
                        help="Live URLs, e.g. "
                             "https://www.youtube.com/@stackswopo_/live "
                             "https://www.twitch.tv/stackswopo . A /clips URL "
                             "is downloaded rather than recorded.")
    parser.add_argument("--name", default="Stackswopo",
                        help="Used in the filename. With several URLs the "
                             "platform is appended, so files stay distinct.")
    parser.add_argument("--staging",
                        default=os.path.join(root, "auto_uploader", "recording"))
    parser.add_argument("--watch-folder",
                        default=os.path.join(root, "auto_uploader", "watch_folder"))
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true",
                        help="Record one stream per source, then exit instead "
                             "of waiting for the next.")
    parser.add_argument("--no-fill-gaps", action="store_true",
                        help="Do not fall back to the published VOD when a "
                             "recording comes up short.")
    parser.add_argument("--clip-limit", type=int, default=0,
                        help="Most clips to fetch from a /clips URL per pass "
                             "(0 = no limit).")
    args = parser.parse_args(argv)

    streams, clip_pages = [], []
    for url in args.url:
        (clip_pages if is_clips_url(url) else streams).append(url)

    def label(url: str) -> str:
        # Two recorders writing the same base filename would fight over
        # the same segment paths, so the platform goes in the name as
        # soon as there is more than one source.
        if len(args.url) == 1:
            return args.name
        kind = "clips" if is_clips_url(url) else "live"
        return f"{args.name} {platform_of(url)} {kind}"

    print("=" * 62)
    print(f" Recording : {args.name}")
    for url in streams:
        print(f" Live      : {url}")
    for url in clip_pages:
        print(f" Clips     : {url}")
    print(f" Delivers  : {args.watch_folder}")
    print("=" * 62)
    print(" Leave this window open. Ctrl+C stops.\n")

    stop = threading.Event()
    threads = []
    for url in streams:
        recorder = Recorder(url=url, staging=args.staging,
                            watch_folder=args.watch_folder, name=label(url),
                            poll_seconds=args.poll_seconds,
                            fill_gaps=not args.no_fill_gaps)
        thread = threading.Thread(target=_source_loop,
                                  args=(recorder, args.once, stop),
                                  name=f"record-{platform_of(url)}", daemon=True)
        thread.start()
        threads.append(thread)

    try:
        # Clips are a poll, not a recording: check them on the same
        # cadence in this thread while the recorders run in theirs.
        while True:
            for url in clip_pages:
                fetch_clips(url, args.staging, args.watch_folder,
                            name=label(url), limit=args.clip_limit)
            if args.once and not threads:
                return 0
            if not threads and not clip_pages:
                return 0
            if args.once and all(not t.is_alive() for t in threads):
                return 0
            # Clips appear far more slowly than streams start, so this
            # deliberately does not poll on poll_seconds.
            stop.wait(max(args.poll_seconds, 900) if clip_pages else 3600)
            if args.once and all(not t.is_alive() for t in threads):
                return 0
    except KeyboardInterrupt:
        stop.set()
        print("\nStopped.")
        for t in threads:
            try:
                t.join(timeout=2)
            except Exception:
                pass
        return 0


if __name__ == "__main__":
    sys.exit(main())

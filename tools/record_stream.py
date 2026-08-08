"""
Records live streams end to end, and survives the network.

YouTube and Twitch, several at once, all delivering into the same watch
folder. A /clips URL is downloaded rather than recorded - clips are
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
import time
from dataclasses import dataclass
from typing import Optional

# A stream that ends and restarts within this window is treated as one
# interrupted recording rather than two streams.
RESUME_WINDOW_S = 90
# Give up resuming after this many consecutive restarts; past this it is
# not a blip, and spinning forever would fill the disk with fragments.
MAX_RESUMES = 20

SAFE_CHARS = " -_.,'!()[]"

# While waiting for a channel to go live, say so this often instead of
# echoing yt-dlp's per-minute countdown. Long enough to be quiet
# overnight, short enough to prove the window is still alive.
WAIT_HEARTBEAT_S = 1800

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


def expected_duration(url: str) -> Optional[float]:
    """How long the stream actually was, according to the platform."""
    try:
        completed = subprocess.run(
            ["yt-dlp", "--no-warnings", "--skip-download",
             "--print", "duration", url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    try:
        return float(completed.stdout.decode().strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


PLATFORM_YOUTUBE = "youtube"
PLATFORM_TWITCH = "twitch"


def platform_of(url: str) -> str:
    """Which site this URL is on. Decides which flags are legal.

    Not cosmetic: --live-from-start is a YouTube-only capability (it walks
    back through the DASH manifest's sequence numbers). Twitch has no
    equivalent, and passing it there produces a warning and no benefit.
    """
    lowered = (url or "").lower()
    if "twitch.tv" in lowered:
        return PLATFORM_TWITCH
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


def is_recording_line(line: str) -> bool:
    """True once bytes are actually being fetched."""
    if any(marker in line for marker in _RECORDING_MARKERS):
        return True
    # "[download]  12.3% of ~4.20GiB" - progress, but not the bare
    # "[download] Downloading playlist" announcements.
    return "[download]" in line and "%" in line


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
    return [
        "yt-dlp",
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

    def say(self, message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    # ── The yt-dlp invocation ────────────────────────────────────────────

    def download_args(self, output_path: str, wait: bool = True) -> list:
        args = [
            "yt-dlp",
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
                os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
                log = open(log_path, "a", encoding="utf-8")
                log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                          f"{' '.join(args)}\n")
            # Suppression is opt-in per call: a clips download has no
            # "waiting for live" phase, and running it through this state
            # machine would report it as a stream starting.
            waiting = quiet_wait
            waiting_since = last_heartbeat = 0.0
            for line in process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-40]
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
                    elif is_worth_saying(line):
                        print(line, flush=True)
                        continue
                    else:
                        now = time.time()
                        if not waiting_since:
                            waiting_since = last_heartbeat = now
                            self.say(f"Not live yet - checking every "
                                     f"{self.poll_seconds}s. This window can "
                                     "stay open for days; each check is one "
                                     "page fetch, so it costs practically no "
                                     "data.")
                        elif now - last_heartbeat >= WAIT_HEARTBEAT_S:
                            self.say(f"Still waiting "
                                     f"({(now - waiting_since) / 3600:.1f}h). "
                                     f"Full detail: {log_path or 'the log'}")
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
            if tail and process.returncode not in (0, None):
                self.say("Last thing yt-dlp said before stopping:")
                for line in tail[-6:]:
                    self.say(f"    {line}")

    # ── Assembling and delivering ────────────────────────────────────────

    def finalise(self, base: str) -> Optional[str]:
        """Join the segments and move the result into the watch folder."""
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
        if report.startswith("SHORT") and self.fill_gaps:
            replaced = self._replace_with_vod(base, destination)
            if replaced:
                destination = replaced
                report = coverage_report(probe_duration(destination),
                                         expected_duration(self.url))
                self.say(report)
        if report.startswith("SHORT"):
            self._notify_short(report)

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
        """TS -> MP4 without re-encoding. Fast, lossless, and gives the
        file the index that makes it seekable."""
        return self._ffmpeg(["-i", source, "-c", "copy",
                             "-movflags", "+faststart", target])

    def _concat(self, segments: list, target: str) -> bool:
        list_path = os.path.join(self.staging, "_concat.txt")
        build_concat_list(segments, list_path)
        ok = self._ffmpeg(["-f", "concat", "-safe", "0", "-i", list_path,
                           "-c", "copy", "-movflags", "+faststart", target])
        try:
            os.remove(list_path)
        except OSError:
            pass
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
        base = f"{safe_name(self.name)} {time.strftime('%Y-%m-%d %H_%M')}"
        log_path = os.path.join(self.staging, f"{base}.log")

        warning = disk_warning(self.staging)
        if warning:
            self.say(f"WARNING: {warning}")

        self.say(f"Waiting for {self.name} to go live...")
        resumes = 0
        while True:
            target = segment_path(self.staging, base, resumes + 1)
            started = time.time()
            # Held only while actually downloading, so the machine can
            # still sleep normally during the wait between streams.
            with KeepAwake() as awake:
                if resumes == 0 and awake.active:
                    self.say("Holding the machine awake for the recording.")
                code = self._run(self.download_args(target, wait=(resumes == 0)),
                                 log_path)
            ended = time.time()

            if code in (127, 130):
                return None
            if code == 0:
                self.say("Stream ended.")
                break
            if not should_resume(started, ended, resumes):
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
    args = [
        "yt-dlp",
        "--fragment-retries", "infinite",
        "--retries", "infinite",
        "--socket-timeout", "30",
        "--concurrent-fragments", "4",
        "--download-archive", archive_path,
        "--no-post-overwrites",
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
        return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Records a YouTube live stream end to end, and survives the network.

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
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# A stream that ends and restarts within this window is treated as one
# interrupted recording rather than two streams.
RESUME_WINDOW_S = 90
# Give up resuming after this many consecutive restarts; past this it is
# not a blip, and spinning forever would fill the disk with fragments.
MAX_RESUMES = 20

SAFE_CHARS = " -_.,'!()[]"


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


def existing_segments(staging: str, base: str) -> list:
    """Every finished segment for this recording, in order."""
    if not os.path.isdir(staging):
        return []
    prefix = f"{base}.part"
    found = [os.path.join(staging, name) for name in sorted(os.listdir(staging))
             if name.startswith(prefix) and name.endswith(".ts")]
    return [p for p in found if os.path.getsize(p) > 0]


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
    _log: list = field(default_factory=list, repr=False)

    def say(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        self._log.append(line)
        print(line, flush=True)

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
            "--live-from-start",
            "--no-playlist",
            "--concurrent-fragments", str(self.concurrent_fragments),
            "--no-progress",
            "--newline",
            "-o", output_path,
        ]
        if wait:
            args += ["--wait-for-video", str(self.poll_seconds)]
        args.append(self.url)
        return args

    def _run(self, args: list) -> int:
        try:
            return subprocess.call(args)
        except FileNotFoundError:
            self.say("ERROR: yt-dlp is not on PATH. Install it: pip install -U yt-dlp")
            return 127
        except KeyboardInterrupt:
            self.say("Stopped by Ctrl+C.")
            return 130

    # ── Assembling and delivering ────────────────────────────────────────

    def finalise(self, base: str) -> Optional[str]:
        """Join the segments and move the result into the watch folder."""
        segments = existing_segments(self.staging, base)
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

        destination = os.path.join(self.watch_folder, os.path.basename(final))
        os.makedirs(self.watch_folder, exist_ok=True)
        try:
            shutil.move(final, destination)
        except OSError as exc:
            self.say(f"Could not move the finished file: {exc}")
            return None

        for path in segments:
            try:
                os.remove(path)
            except OSError:
                pass
        self.say(f"Delivered -> {destination}")
        self.say("The uploader will pick it up (run: python main.py --watch)")
        return destination

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

        self.say(f"Waiting for {self.name} to go live...")
        resumes = 0
        while True:
            target = segment_path(self.staging, base, resumes + 1)
            started = time.time()
            code = self._run(self.download_args(target, wait=(resumes == 0)))
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
            time.sleep(3)

        return self.finalise(base)


def main(argv: Optional[list] = None) -> int:
    import argparse

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)

    parser = argparse.ArgumentParser(
        description="Record a live stream into the uploader's watch folder.")
    parser.add_argument("url", help="Channel live URL, e.g. "
                                    "https://www.youtube.com/@stackswopo_/live")
    parser.add_argument("--name", default="Stackswopo",
                        help="Used in the filename.")
    parser.add_argument("--staging",
                        default=os.path.join(root, "auto_uploader", "recording"))
    parser.add_argument("--watch-folder",
                        default=os.path.join(root, "auto_uploader", "watch_folder"))
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true",
                        help="Record one stream, then exit instead of waiting "
                             "for the next.")
    args = parser.parse_args(argv)

    recorder = Recorder(url=args.url, staging=args.staging,
                        watch_folder=args.watch_folder, name=args.name,
                        poll_seconds=args.poll_seconds)

    print("=" * 62)
    print(f" Recording : {args.name}")
    print(f" Source    : {args.url}")
    print(f" Delivers  : {args.watch_folder}")
    print("=" * 62)
    print(" Leave this window open. Ctrl+C stops.\n")

    try:
        while True:
            recorder.record_one_stream()
            if args.once:
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

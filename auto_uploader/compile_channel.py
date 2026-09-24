"""
compile_channel.py - long back-to-back compilations of Stackswopo's own
channel, uploaded to the VOD channel. Run it with COMPILE.bat.

WHAT IT MAKES
    The same thing the Stackswopo compilation channels post - "2 Hours of
    Whiteboy Trolling Clips #93", "1 hour of stackswopo funniest moments" -
    and made the same way they make it: several of Stackswopo's own edited
    videos, whole, one after another, under a numbered series title, with
    the thumbnail of the most-watched video in it.

    What it adds over theirs: every video is normalised to one size, frame
    rate and loudness (their uploads jump in volume between videos), a
    short fade between videos, chapters, and a description that credits
    and links every original.

WHERE THE FOOTAGE COMES FROM
    compilation.sources in config.json - Stackswopo's own channel by
    default, reposted to his own second channel (STACKSWOPOVODS,
    @STACKSWOPO10K) with his OK. NOT the other compilation channels: every one of them is a
    re-cut of that same channel, so pulling from them is re-uploading
    somebody else's re-upload - the same videos again, a generation worse,
    and the thing channel_vods.py already declines to do ("other people's
    cuts ... reposted as yours").

"DIFFERENT FOOTAGE" EVERY TIME
    compilation_ledger.json remembers every source video ever used. A
    video goes into one compilation and never another, and each run takes
    the next series in rotation (Whiteboy Trolling Clips, Funny Moments,
    Stream Recaps, ...), each with its own running number.

STORAGE
    Everything is built under compilation.work_folder and deleted once
    the upload is confirmed. Each download is deleted as soon as its
    normalised copy exists, so the peak is roughly one finished
    compilation plus one source video.

    If the upload fails, the finished file is kept and the next run
    uploads it instead of building a new one.

USAGE
    python compile_channel.py            build one compilation and upload it
    python compile_channel.py --plan     show what it would use, change nothing
    python compile_channel.py --no-upload   build it, keep it, do not upload
    python compile_channel.py --minutes 60  a one-hour one this time
    python compile_channel.py --count 3     three in a row
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ── settings ────────────────────────────────────────────────────────────────

DEFAULTS = {
    "sources": ["https://www.youtube.com/@stackswopo_/videos"],
    # Linked in every description as where the footage is from.
    "credit_url": "https://www.youtube.com/@stackswopo_",
    "credit_name": "Stackswopo",
    "target_minutes": 120,
    # Shorts are not compilation material, and a multi-hour upload is a
    # stream VOD rather than an edit.
    "min_source_minutes": 8,
    "max_source_minutes": 60,
    # How far back in each channel to look.
    "scan_limit": 400,
    # Reactions are other people's videos with Stackswopo on top - a
    # Content ID claim waiting to happen, and not his footage to reuse.
    "skip_title_words": ["react", "reacts", "reacting", "trailer"],
    # A title needs this many "#N" videos to count as a series.
    "min_series_size": 3,
    "mixed_series_label": "Stackswopo Best Moments",
    "title_format": "{length} of {series} #{number}",
    "tags": ["Stackswopo", "Stackswopo Funny Moments", "GTA RP", "GTA 5",
             "FiveM", "Roleplay", "Compilation", "Funny Moments",
             "Whiteboy Trolling"],
    "privacy": "public",
    "category_id": "20",
    # The upload refuses to go anywhere else. Matched against the channel
    # name AND its @handle - STACKSWOPOVODS is @STACKSWOPO10K. Empty = no
    # check.
    "expect_channel": "@STACKSWOPO10K",
    # Its own login, separate from the VOD uploader's youtube_token.json
    # (that one is @StackswopoGames). The first run opens a browser to sign
    # in: pick STACKSWOPOVODS there.
    "token_path": "youtube_compilation_token.json",
    "cookies": "cookies.txt",
    "work_folder": "./compilations",
    "ledger_path": "./compilation_ledger.json",
    "delete_after_upload": True,
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "fade_seconds": 0.5,
    # YouTube's own loudness target, so it does not turn the video down.
    "loudness_lufs": -14,
    "encoder": "auto",
}


def load_settings(config_path: str) -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        raw = {}
    settings = dict(DEFAULTS)
    settings.update({key: value for key, value in
                     (raw.get("compilation") or {}).items()
                     if not key.startswith("_")})
    return settings


def _resolve(path: str) -> str:
    return path if os.path.isabs(path) else os.path.normpath(
        os.path.join(HERE, path))


# ── what is on the channel ──────────────────────────────────────────────────

@dataclass
class Video:
    id: str
    title: str
    duration: float
    views: int = 0
    channel: str = ""
    series: str = ""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


def ytdlp_command() -> list:
    try:
        import yt_dlp  # noqa: F401
    except Exception:
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def update_yt_dlp(runner=None) -> tuple:
    """(updated, detail). Never raises.

    With the "default" extra, not bare: that extra pins yt-dlp-ejs, the
    script that solves YouTube's download challenges, to one exact
    version. Upgrading yt-dlp alone leaves the old solver behind.
    """
    runner = runner or subprocess.run
    command = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"]
    try:
        done = runner(command, capture_output=True, text=True, timeout=300)
    except Exception as exc:
        return False, str(exc)
    if getattr(done, "returncode", 1) != 0:
        detail = (getattr(done, "stderr", "") or "").strip().splitlines()
        return False, (detail[-1] if detail else "pip failed")
    out = getattr(done, "stdout", "") or ""
    if "Successfully installed" in out:
        for word in out.split():
            if word.startswith("yt-dlp-") and word[7:8].isdigit():
                return True, f"updated to {word[7:]}"
        return True, "updated"
    return True, "already the latest version"


def _refused(stderr: str) -> bool:
    return bool(re.search(r"HTTP Error 403|Forbidden", stderr or ""))


def _deno_from_pip() -> str:
    """The deno binary the `deno` pip package ships, installing that
    package once if it is missing. It lands in Python's Scripts folder,
    which is usually not on PATH on Windows - so it is found by asking the
    package, not by searching PATH."""
    for attempt in range(2):
        try:
            import deno  # type: ignore

            path = deno.find_deno_bin()
            if path and os.path.isfile(path):
                return path
        except Exception:
            pass
        if attempt == 0:
            _say("Installing Deno (YouTube needs it for the audio and the "
                 "full-quality video) ...")
            subprocess.run([sys.executable, "-m", "pip", "install", "--user",
                            "-q", "deno"], capture_output=True, timeout=600)
    return ""


def _js_runtime_args() -> list:
    """YouTube's audio and full-quality video URLs are scrambled, and
    yt-dlp needs a JavaScript runtime to unscramble them. Without one it
    still LISTS every format - and then the download of the scrambled ones
    fails with 403 Forbidden. That is exactly the first real run: the HLS
    video (not scrambled) came down, the audio did not, and ffmpeg was
    handed a video with no sound. yt-dlp only looks for deno on PATH; any
    other runtime, or a deno somewhere else, has to be named."""
    if shutil.which("deno"):
        return []
    found = _deno_from_pip()
    if found:
        return ["--js-runtimes", f"deno:{found}"]
    for runtime in ("node", "bun"):
        if shutil.which(runtime):
            return ["--js-runtimes", runtime]
    raise RuntimeError(
        "no JavaScript runtime for yt-dlp, so YouTube will not hand over "
        "the audio. Run:  python -m pip install --user deno")


# Plain https H.264 first - it is what every browser plays, it needs no
# re-download of HLS fragments, and it is what the normalise pass decodes
# fastest. The ORIGINAL audio track by name: these videos carry dubbed
# French/Spanish/German/Portuguese tracks too, and a bare "ba" can pick
# one of those.
FORMAT = ("bv*[height<={h}][vcodec^=avc1][protocol^=https]"
          "+ba[format_note*=original]/"
          "bv*[height<={h}]+ba[format_note*=original]/"
          "bv*[height<={h}]+ba/"
          "b[height<={h}]/b")


def _cookie_args(settings: dict) -> list:
    cookies = settings.get("cookies") or ""
    if cookies and os.path.isfile(_resolve(cookies)):
        return ["--cookies", _resolve(cookies)]
    browser = settings.get("cookies_from_browser") or ""
    return ["--cookies-from-browser", browser] if browser else []


_FIELD_SEP = "\t"


def parse_listing(output: str, channel: str = "") -> List[Video]:
    """yt-dlp --print lines (id, duration, views, title) -> Videos.

    Anything without a duration is dropped: nothing can be planned around
    a video of unknown length, and upcoming premieres and live streams
    are exactly the entries that have none.
    """
    videos = []
    for line in output.splitlines():
        parts = line.split(_FIELD_SEP, 3)
        if len(parts) != 4:
            continue
        vid, duration, views, title = parts
        try:
            seconds = float(duration)
        except ValueError:
            continue
        try:
            count = int(views)
        except ValueError:
            count = 0
        videos.append(Video(vid.strip(), title.strip(), seconds, count,
                            channel))
    return videos


def list_channel(url: str, settings: dict) -> List[Video]:
    command = ytdlp_command() + _cookie_args(settings) + [
        "--flat-playlist", "--ignore-errors", "--no-warnings",
        "--playlist-end", str(int(settings["scan_limit"])),
        "--print", _FIELD_SEP.join(
            ["%(id)s", "%(duration)s", "%(view_count)s", "%(title)s"]),
        url,
    ]
    done = subprocess.run(command, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    videos = parse_listing(done.stdout, url)
    if not videos and done.returncode != 0:
        raise RuntimeError(f"could not read {url}: "
                           f"{(done.stderr or '').strip()[-400:]}")
    return videos


# ── series ──────────────────────────────────────────────────────────────────

_NUMBERED = re.compile(r"^(.*?)\s*#\s*\d+\b")


def series_of(title: str) -> str:
    """"Whiteboy Trolling Clips #126 (TOP TIER)" -> "Whiteboy Trolling
    Clips". Titles without a number have no series."""
    match = _NUMBERED.match(title or "")
    if not match:
        return ""
    name = re.sub(r"[\s\-|:]+$", "", match.group(1)).strip()
    return name


def eligible(videos: List[Video], used: set, settings: dict) -> List[Video]:
    low = float(settings["min_source_minutes"]) * 60
    high = float(settings["max_source_minutes"]) * 60
    skip = [w.lower() for w in settings.get("skip_title_words") or ()]
    seen = set()
    keep = []
    for video in videos:
        words = re.findall(r"[a-z0-9]+", video.title.lower())
        if (video.id in used or video.id in seen
                or not low <= video.duration <= high
                or "shorts" in words
                or any(w in words for w in skip)):
            continue
        seen.add(video.id)
        keep.append(video)
    return keep


def group_series(videos: List[Video], settings: dict) -> Dict[str, List[Video]]:
    """Series with enough videos to fill a compilation's title honestly;
    everything else goes in the mixed pool under mixed_series_label."""
    groups: Dict[str, List[Video]] = {}
    for video in videos:
        groups.setdefault(series_of(video.title), []).append(video)
    mixed = settings["mixed_series_label"]
    result: Dict[str, List[Video]] = {}
    for name, members in groups.items():
        if name and len(members) >= int(settings["min_series_size"]):
            result[name] = members
        else:
            result.setdefault(mixed, []).extend(members)
    for name, members in result.items():
        for video in members:
            video.series = name
    return result


# ── the ledger: what has been used, what number is next ─────────────────────

class Ledger:
    def __init__(self, path: str):
        self.path = path
        self.data = {"used": {}, "numbers": {}, "last_series": "",
                     "uploads": []}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                self.data.update(json.load(handle))
        except (OSError, ValueError):
            pass

    @property
    def used(self) -> set:
        return set(self.data["used"])

    def next_number(self, series: str) -> int:
        return int(self.data["numbers"].get(series, 0)) + 1

    def record(self, series: str, number: int, videos: List[Video],
               url: str, title: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M")
        for video in videos:
            self.data["used"][video.id] = {"title": video.title,
                                           "compilation": title}
        self.data["numbers"][series] = number
        self.data["last_series"] = series
        self.data["uploads"].append({"when": stamp, "title": title,
                                     "url": url,
                                     "videos": [v.id for v in videos]})
        self.save()

    def save(self) -> None:
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, indent=2, ensure_ascii=False)
        os.replace(temp, self.path)


# ── choosing ────────────────────────────────────────────────────────────────

def _fill(pool: List[Video], target: float) -> List[Video]:
    picked, total = [], 0.0
    for video in pool:
        if total >= target:
            break
        picked.append(video)
        total += video.duration
    return picked


def choose(videos: List[Video], ledger: Ledger, settings: dict,
           only_series: str = "") -> tuple:
    """(series label, videos in play order) for the next compilation.

    Series take turns: the one after last time's, if it still has enough
    unused footage to fill the target, else the next one that does. If no
    single series can, the compilation mixes all of them - round-robin so
    it does not open with forty minutes of one kind.
    """
    target = float(settings["target_minutes"]) * 60
    groups = group_series(eligible(videos, ledger.used, settings), settings)
    if not groups:
        return "", []

    mixed = settings["mixed_series_label"]
    # Biggest series first, the one-offs last: rotation order is stable
    # from run to run, which is what makes "the next one" mean something.
    # The one-offs (Johnny Cox, the Halloween game, ...) take a turn like
    # any series - they are a third of the channel.
    order = sorted((n for n in groups if n != mixed),
                   key=lambda n: (-len(groups[n]), n))
    if mixed in groups:
        order.append(mixed)
    if only_series:
        order = [n for n in order if n.lower() == only_series.lower()]
    else:
        last = ledger.data.get("last_series") or ""
        if last in order:
            at = order.index(last) + 1
            order = order[at:] + order[:at]

    for name in order:
        pool = groups[name]
        if sum(v.duration for v in pool) >= target * 0.9:
            # Listings are newest first; take the newest unused, then play
            # them oldest first so the compilation runs forward in time.
            return name, list(reversed(_fill(pool, target)))

    if only_series:
        return "", []
    # Round-robin across everything that is left.
    queues = [list(members) for members in groups.values()]
    mixed_pool = []
    while any(queues):
        for queue in queues:
            if queue:
                mixed_pool.append(queue.pop(0))
    picked = _fill(mixed_pool, target)
    if sum(v.duration for v in picked) < target * 0.5:
        return "", []
    return mixed, picked


# ── words ───────────────────────────────────────────────────────────────────

def length_label(seconds: float) -> str:
    hours = seconds / 3600
    if hours < 0.95:
        minutes = max(1, int(round(seconds / 60)))
        return "1 Minute" if minutes == 1 else f"{minutes} Minutes"
    whole = int(round(hours))
    return "1 Hour" if whole == 1 else f"{whole} Hours"


def make_title(series: str, number: int, seconds: float,
               settings: dict) -> str:
    title = settings["title_format"].format(
        length=length_label(seconds), series=series, number=number)
    return title[:100]


def timestamp(seconds: float, long_form: bool) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if long_form else f"{m:02d}:{s:02d}"


def chapters(videos: List[Video], durations: List[float]) -> str:
    """YouTube chapter lines: the first at zero, one per source video."""
    long_form = sum(durations) >= 3600
    lines, at = [], 0.0
    for video, length in zip(videos, durations):
        lines.append(f"{timestamp(at, long_form)} {video.title}")
        at += length
    return "\n".join(lines)


def make_description(series: str, length: str, videos: List[Video],
                     durations: List[float], settings: dict) -> str:
    credit = settings["credit_name"]
    parts = [
        f"{length} of {series}, back to back.",
        "",
        f"All footage is {credit}'s. Watch the originals and subscribe: "
        f"{settings['credit_url']}",
        "",
        "Chapters",
        chapters(videos, durations),
        "",
        "Originals",
    ]
    parts += [f"- {v.title}: https://youtu.be/{v.id}" for v in videos]
    parts += ["", "#stackswopo #gta #gtarp #fivem"]
    text = "\n".join(parts)
    # YouTube's limit is 5000; the originals list is what gets cut.
    return text[:4900]


# ── building ────────────────────────────────────────────────────────────────

def _run(command: list, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _clear_halves(folder: str, video_id: str, merged: str) -> None:
    # Only the MERGED file counts. "<id>.f616.mp4" is one half of a
    # download whose other half failed - taking it is how a video with no
    # sound reached ffmpeg.
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if name.startswith(video_id + ".") and path != merged:
            try:
                os.remove(path)
            except OSError:
                pass


def download(video: Video, folder: str, settings: dict) -> str:
    os.makedirs(folder, exist_ok=True)
    template = os.path.join(folder, f"{video.id}.%(ext)s")
    command = ytdlp_command() + _js_runtime_args() + _cookie_args(settings) + [
        "--no-playlist", "--no-warnings",
        "-f", FORMAT.format(h=settings["height"]),
        "--merge-output-format", "mp4",
        "--retries", "10", "--fragment-retries", "10",
        "--socket-timeout", "30",
        "-o", template, video.url,
    ]
    merged = os.path.join(folder, f"{video.id}.mp4")
    done = _run(command, timeout=3 * 60 * 60)
    _clear_halves(folder, video.id, merged)
    # A sudden 403 on a video that is public and plays fine in a browser is
    # almost always yt-dlp behind YouTube's latest player change - it
    # follows within days. A real run stopped the whole compilation at the
    # first video over exactly this, when a one-minute update fixes it.
    if not os.path.isfile(merged) and _refused(done.stderr):
        _say("  YouTube refused the download (403). That usually means "
             "yt-dlp is out of date - updating it and trying again ...")
        updated, detail = update_yt_dlp()
        if updated:
            _say(f"  yt-dlp {detail}.")
            done = _run(command, timeout=3 * 60 * 60)
            _clear_halves(folder, video.id, merged)
        else:
            _say(f"  Could not update yt-dlp ({detail}).")
    if not os.path.isfile(merged):
        raise RuntimeError(f"download failed for {video.title}: "
                           f"{(done.stderr or '').strip()[-400:]}")
    if not has_audio(merged):
        os.remove(merged)
        raise RuntimeError(f"{video.title} downloaded without its audio. "
                           f"yt-dlp said: {(done.stderr or '').strip()[-300:]}")
    height = probe_height(merged)
    if height and height < 720:
        _say(f"  Note: only {height}p was available for this one.")
    return merged


def has_audio(path: str) -> bool:
    done = _run(["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "csv=p=0", path], 60)
    return bool(done.stdout.strip())


def probe_duration(path: str) -> float:
    done = _run(["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "default=nw=1:nk=1", path], 60)
    try:
        return float(done.stdout.strip())
    except ValueError:
        return 0.0


def probe_height(path: str) -> int:
    done = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=height", "-of",
                 "default=nw=1:nk=1", path], 60)
    try:
        return int(done.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def encoder_args(encoder: str) -> list:
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
                "-cq", "21", "-b:v", "0", "-g", "60"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-g", "60"]


def _nvenc_ok() -> bool:
    """One real test encode at a real-ish size. NVENC refuses frames below
    a minimum width on many cards, so a tiny test frame can fail on a GPU
    that encodes 1080p perfectly well."""
    try:
        done = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                     "-f", "lavfi", "-i", "color=c=black:s=640x360:d=0.5",
                     "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=60)
    except Exception:
        return False
    return done.returncode == 0


def pick_encoder(preference: str) -> str:
    preference = (preference or "auto").strip().lower()
    if preference == "cpu":
        return "libx264"
    if _nvenc_ok():
        return "h264_nvenc"
    _say("NVIDIA encoding is not available to ffmpeg here - using the CPU, "
         "which is several times slower for a two-hour video.")
    return "libx264"


def normalize_command(source: str, target: str, duration: float,
                      settings: dict, encoder: str) -> list:
    """One source video re-encoded to the compilation's single format.

    Every segment must be identical in size, frame rate, pixel format and
    audio layout for the final join to be a plain stream copy, and the
    loudness pass is what stops one video being twice as loud as the
    next. The fades are the edit between videos.
    """
    w, h, fps = settings["width"], settings["height"], settings["fps"]
    fade = float(settings["fade_seconds"])
    out_at = max(0.0, duration - fade)
    video = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
             f"fps={fps},format=yuv420p,setsar=1")
    audio = (f"loudnorm=I={settings['loudness_lufs']}:TP=-1.5:LRA=11,"
             f"aresample=48000")
    if fade > 0:
        video += (f",fade=t=in:st=0:d={fade},"
                  f"fade=t=out:st={out_at:.3f}:d={fade}")
        audio += (f",afade=t=in:st=0:d={fade},"
                  f"afade=t=out:st={out_at:.3f}:d={fade}")
    return (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", source, "-map", "0:v:0", "-map", "0:a:0",
             "-vf", video, "-af", audio]
            + encoder_args(encoder)
            + ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
               "-video_track_timescale", "90000",
               "-movflags", "+faststart", target])


def normalize(source: str, target: str, settings: dict, encoder: str) -> None:
    duration = probe_duration(source)
    if duration <= 0:
        raise RuntimeError(f"cannot read {os.path.basename(source)}")
    done = _run(normalize_command(source, target, duration, settings,
                                  encoder), timeout=4 * 3600)
    if done.returncode != 0 or not os.path.isfile(target):
        if encoder != "libx264":
            # NVENC can fail mid-run (driver, VRAM); CPU always works.
            print("[Compile] GPU encode failed - redoing it on the CPU.")
            normalize(source, target, settings, "libx264")
            return
        raise RuntimeError(f"ffmpeg failed on {os.path.basename(source)}: "
                           f"{(done.stderr or '').strip()[-400:]}")


def concat(segments: List[str], target: str) -> None:
    listing = target + ".txt"
    with open(listing, "w", encoding="utf-8") as handle:
        for path in segments:
            safe = os.path.abspath(path).replace("\\", "/").replace("'", r"'\''")
            handle.write(f"file '{safe}'\n")
    done = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "concat", "-safe", "0", "-i", listing,
                 "-c", "copy", "-movflags", "+faststart", target],
                timeout=3 * 3600)
    os.remove(listing)
    if done.returncode != 0 or not os.path.isfile(target):
        raise RuntimeError(f"joining the videos failed: "
                           f"{(done.stderr or '').strip()[-400:]}")


def fetch_thumbnail(videos: List[Video], folder: str) -> str:
    """The most-watched video's own thumbnail - what the compilation
    channels use, because it is the one already proven to get clicks."""
    best = max(videos, key=lambda v: v.views)
    target = os.path.join(folder, "thumbnail.jpg")
    for size in ("maxresdefault", "hqdefault"):
        try:
            with urllib.request.urlopen(
                    f"https://i.ytimg.com/vi/{best.id}/{size}.jpg",
                    timeout=30) as response:
                data = response.read()
            if len(data) > 5000:
                with open(target, "wb") as handle:
                    handle.write(data)
                return target
        except Exception:
            continue
    return ""


def frame_thumbnail(video_path: str, folder: str, at: float) -> str:
    """A 1280x720 frame from the finished video, for when the source
    thumbnail cannot be fetched - so an upload never goes out with
    YouTube's auto-picked frame."""
    target = os.path.join(folder, "thumbnail.jpg")
    done = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-ss", f"{max(0.0, at):.2f}", "-i", video_path,
                 "-frames:v", "1", "-vf", "scale=1280:720", "-q:v", "2",
                 target], timeout=120)
    return target if done.returncode == 0 and os.path.isfile(target) else ""


def make_thumbnail(videos: List[Video], final: str, total: float,
                   folder: str) -> str:
    path = fetch_thumbnail(videos, folder)
    if path:
        best = max(videos, key=lambda v: v.views)
        _say(f"Thumbnail: from \"{best.title}\" (its own thumbnail)")
        return path
    path = frame_thumbnail(final, folder, total * 0.1)
    _say("Thumbnail: a frame from the video (the source thumbnail could not "
         "be fetched)" if path else "Thumbnail: none - YouTube will pick one")
    return path


# ── uploading ───────────────────────────────────────────────────────────────

def _normal(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def youtube_uploader(settings: dict):
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(HERE, ".env"))
    except Exception:
        pass
    from utils.youtube_uploader import YouTubeUploader

    secrets = os.environ.get("YOUTUBE_CLIENT_SECRETS_PATH",
                             os.path.join(HERE, "client_secrets.json"))
    token = _resolve(settings["token_path"]) if settings.get("token_path") \
        else os.path.join(HERE, "youtube_token.json")
    return YouTubeUploader(secrets, token)


def channel_identity(uploader) -> tuple:
    """(name, @handle) of the channel this login uploads to."""
    items = uploader.get_service().channels().list(
        part="snippet", mine=True).execute().get("items") or []
    if not items:
        return "", ""
    snippet = items[0]["snippet"]
    return snippet.get("title", ""), snippet.get("customUrl", "")


def check_channel(uploader, expected: str, token_path: str = "") -> None:
    if not expected:
        return
    name, handle = channel_identity(uploader)
    if _normal(expected) not in {_normal(name), _normal(handle)}:
        where = f" ({token_path})" if token_path else ""
        raise RuntimeError(
            f"the YouTube login{where} is for \"{name}\" {handle}, not "
            f"{expected}. Nothing was uploaded. Delete that token file and "
            f"run again - a browser opens to sign in; pick {expected} there.")
    _say(f"Uploading to {name} {handle}")


# ── one run ─────────────────────────────────────────────────────────────────

PENDING = "pending.json"


def _say(text: str) -> None:
    print(f"[Compile] {text}", flush=True)


def plan(settings: dict, ledger: Ledger, only_series: str = "") -> tuple:
    videos: List[Video] = []
    for source in settings["sources"]:
        _say(f"Reading {source} ...")
        videos += list_channel(source, settings)
    _say(f"{len(videos)} videos listed, "
         f"{len(eligible(videos, ledger.used, settings))} usable and "
         f"not used yet.")
    return choose(videos, ledger, settings, only_series)


def build(series: str, picks: List[Video], settings: dict,
          ledger: Ledger, work: str) -> dict:
    os.makedirs(work, exist_ok=True)
    encoder = pick_encoder(settings.get("encoder", "auto"))
    _say(f"Encoder: {encoder}")
    segments, durations = [], []
    for index, video in enumerate(picks, 1):
        _say(f"[{index}/{len(picks)}] {video.title} "
             f"({timestamp(video.duration, video.duration >= 3600)})")
        source = download(video, os.path.join(work, "downloads"), settings)
        segment = os.path.join(work, f"part{index:02d}.mp4")
        normalize(source, segment, settings, encoder)
        # The download is not needed once its normalised copy exists.
        os.remove(source)
        segments.append(segment)
        durations.append(probe_duration(segment))

    shutil.rmtree(os.path.join(work, "downloads"), ignore_errors=True)
    final = os.path.join(work, "compilation.mp4")
    _say("Joining ...")
    concat(segments, final)
    for segment in segments:
        os.remove(segment)

    total = sum(durations)
    number = ledger.next_number(series)
    length = length_label(total)
    job = {
        "final": final,
        "series": series,
        "number": number,
        "title": make_title(series, number, total, settings),
        "description": make_description(series, length, picks, durations,
                                        settings),
        "tags": list(settings["tags"]),
        "thumbnail": make_thumbnail(picks, final, total, work),
        "videos": [vars(v) for v in picks],
    }
    with open(os.path.join(work, PENDING), "w", encoding="utf-8") as handle:
        json.dump(job, handle, indent=2, ensure_ascii=False)
    return job


def upload(job: dict, settings: dict, ledger: Ledger, work: str) -> str:
    token = settings.get("token_path") or ""
    if token and not os.path.isfile(_resolve(token)):
        _say(f"First upload to this channel: a browser will open to sign in. "
             f"Choose {settings.get('expect_channel') or 'the channel'} "
             f"there.")
    uploader = youtube_uploader(settings)
    check_channel(uploader, settings.get("expect_channel", ""), token)
    _say(f"Uploading \"{job['title']}\" ...")
    last = [-1]

    def progress(percent):
        if percent // 10 != last[0]:
            last[0] = percent // 10
            _say(f"  {percent}%")

    url = uploader.upload(
        job["final"], job["title"], job["description"], job["tags"],
        privacy=settings["privacy"], category_id=str(settings["category_id"]),
        thumbnail_path=job.get("thumbnail") or None,
        progress_callback=progress)
    videos = [Video(**v) for v in job["videos"]]
    ledger.record(job["series"], int(job["number"]), videos, url,
                  job["title"])
    _say(f"Uploaded: {url}")
    if settings.get("delete_after_upload", True):
        shutil.rmtree(work, ignore_errors=True)
        _say("Deleted the working files.")
    return url


def run(argv: Optional[list] = None) -> int:
    """--count N makes N compilations in a row, each with fresh footage."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    count = 1
    if "--count" in args_list:
        at = args_list.index("--count")
        count = max(1, int(args_list[at + 1]))
        del args_list[at:at + 2]
    for turn in range(count):
        if count > 1:
            _say(f"=== Compilation {turn + 1} of {count} ===")
        code = run_once(args_list)
        if code != 0:
            return code
    return 0


def run_once(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plan", action="store_true",
                        help="list what the next compilation would use")
    parser.add_argument("--no-upload", action="store_true",
                        help="build it and keep it; do not upload")
    parser.add_argument("--minutes", type=float,
                        help="target length this time")
    parser.add_argument("--series", default="",
                        help="use this series instead of the next in turn")
    parser.add_argument("--config", default=os.path.join(HERE, "config.json"))
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    if args.minutes:
        settings["target_minutes"] = args.minutes
    ledger = Ledger(_resolve(settings["ledger_path"]))
    work = _resolve(settings["work_folder"])

    # A finished compilation whose upload failed is uploaded, not rebuilt.
    pending = os.path.join(work, PENDING)
    if os.path.isfile(pending) and not args.plan:
        with open(pending, "r", encoding="utf-8") as handle:
            job = json.load(handle)
        if os.path.isfile(job.get("final", "")):
            _say(f"Found \"{job['title']}\" from last time, not uploaded yet.")
            if args.no_upload:
                return 0
            upload(job, settings, ledger, work)
            return 0

    series, picks = plan(settings, ledger, args.series)
    if not picks:
        _say("Not enough unused footage for a compilation. Lower "
             "compilation.target_minutes or add a source.")
        return 1
    total = sum(v.duration for v in picks)
    _say(f"Next: {make_title(series, ledger.next_number(series), total, settings)}")
    for video in picks:
        _say(f"   {timestamp(video.duration, True)}  {video.title}")
    if args.plan:
        return 0

    # Anything left from a run that died mid-build is stale.
    shutil.rmtree(work, ignore_errors=True)
    job = build(series, picks, settings, ledger, work)
    _say(f"Built: {job['final']}")
    if args.no_upload:
        _say("--no-upload: kept it. Run again without it to upload.")
        return 0
    upload(job, settings, ledger, work)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # the .bat window should say why, not trace
        print(f"[Compile] Stopped: {exc}")
        sys.exit(1)

"""Instagram Story for each new full stream - the IG side of the X
"New upload" post (owner, 11-02: "why you don't do it on Instagram too").

Instagram has no link/text post through the API, but a Story is a video
and goes up through the same resumable upload as a Reel. So: the
stream's own YouTube thumbnail, made into an 8-second 9:16 card - "NEW
FULL STREAM", the title, "OUT NOW ON YOUTUBE - LINK IN BIO" - posted as
a Story. Stories do not use a Reel slot (own guard key `instagram_story`).

The thumbnail, never a random frame: it was already picked and checked
for the upload (a random frame landed on a kid on screen in testing).
No thumbnail -> no Story.

Best-effort and off the upload path: render + post run in a process of
their own (logs/ig_story.log), so an uploader restart can't kill them.
A failure here never touches the upload.
"""

import os
import subprocess
import tempfile
import textwrap

SECONDS = 8


def _font(*candidates: str) -> str:
    """The first font file that exists, escaped for an ffmpeg filter
    argument - or "" (ffmpeg's own default) if none of them do.

    A fresh Windows install does not ship every font back (10-28 lost
    the CapCut intro the same way), and unlike this box's ffmpeg,
    typical Windows ffmpeg builds have no fontconfig to fall back to on
    a bad fontfile path - drawtext just fails, silently taking the
    whole Story render down with it. Same reasoning as
    live_chat_overlay.font_file(), which exists for the same risk.
    """
    for path in candidates:
        if path and os.path.isfile(path):
            return path.replace("\\", "/").replace(":", "\\:")
    return ""


def wrap_title(title: str, width: int = 26, lines: int = 3) -> str:
    """The title as up to three lines that fit 1080 px at 60 px."""
    from utils import text_guard

    clean = text_guard.text(" ".join(str(title or "").split()), "post")
    rows = textwrap.wrap(clean, width) or ["Stackswopo"]
    if len(rows) > lines:
        rows = rows[:lines]
        rows[-1] = rows[-1].rstrip(" .,-|") + "…"
    return "\n".join(rows)


def render_story(image_path: str, title: str, out_mp4: str) -> bool:
    """1080x1920, 8 s: blurred background, the thumbnail in the middle,
    text above and below. Text goes through a file so no title can break
    the filter string."""
    folder = os.path.dirname(out_mp4)
    title_file = os.path.join(folder, "story_title.txt")
    # newline="": a Windows \r\n is a second line break to drawtext.
    with open(title_file, "w", encoding="utf-8", newline="") as handle:
        handle.write(wrap_title(title))
    tf = title_file.replace("\\", "/").replace(":", "\\:")
    dejavu = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    heavy = _font(r"C:\Windows\Fonts\impact.ttf", r"C:\Windows\Fonts\arialbd.ttf", dejavu)
    heavy_opt = f"fontfile='{heavy}':" if heavy else ""
    font = _font(r"C:\Windows\Fonts\arialbd.ttf", dejavu)
    font_opt = f"fontfile='{font}':" if font else ""
    graph = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=30:5,eq=brightness=-0.25[bg];"
        "[0:v]scale=1000:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2+40,"
        f"drawtext={heavy_opt}text='NEW FULL STREAM':fontsize=110:"
        "fontcolor=yellow:borderw=6:bordercolor=black:x=(w-tw)/2:y=200,"
        # expansion=none: a % in a title ("100%") is text, not a code.
        f"drawtext={font_opt}textfile='{tf}':expansion=none:fontsize=60:"
        "line_spacing=12:fontcolor=white:borderw=4:bordercolor=black:"
        "x=(w-tw)/2:y=350,"
        f"drawtext={heavy_opt}text='OUT NOW ON YOUTUBE':fontsize=84:"
        "fontcolor=white:borderw=5:bordercolor=black:x=(w-tw)/2:y=h-470,"
        f"drawtext={heavy_opt}text='LINK IN BIO':fontsize=96:"
        "fontcolor=yellow:borderw=6:bordercolor=black:x=(w-tw)/2:y=h-360,"
        "format=yuv420p[v]"
    )
    cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", str(SECONDS),
           "-i", image_path, "-f", "lavfi", "-t", str(SECONDS),
           "-i", "anullsrc=r=44100:cl=stereo", "-filter_complex", graph,
           "-map", "[v]", "-map", "1:a", "-r", "30", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", "-shortest", out_mp4]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as exc:
        print(f"[IG Story] render failed: {exc}")
        return False
    if result.returncode != 0:
        print(f"[IG Story] render failed: {result.stderr.strip()[-300:]}")
        return False
    return os.path.isfile(out_mp4)


def post_story(posting: dict, story_mp4: str, config: dict = None) -> bool:
    """Behind the guard (kill switch, cap, spacing, breaker) like every post."""
    from publish_guard import PublishGuard
    from publishers.instagram import InstagramPublisher

    guard = PublishGuard(posting or {}, (posting or {}).get("state_path"))
    allowed, reason = guard.can_post("instagram_story")
    if not allowed:
        print(f"[IG Story] skipped - {reason}")
        return False
    publisher = InstagramPublisher(config or {})
    if not publisher.ready():
        print("[IG Story] skipped - Instagram not configured")
        return False
    try:
        ok = publisher.post_story_from_file(story_mp4)
    except Exception as exc:
        print(f"[IG Story] upload raised {exc}")
        ok = False
    guard.record_result("instagram_story", ok)
    print(f"[IG Story] {'posted the new-stream Story' if ok else 'failed'}.")
    return ok


def post_later(cfg, thumbnail_path: str, title: str) -> None:
    """Render + post from a process of its own. Never raises."""
    posting = getattr(cfg, "posting", None) or {}
    if not posting or not thumbnail_path or not os.path.isfile(thumbnail_path):
        print("[IG Story] no thumbnail for this stream - skipped")
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    # A process of its own, like the Posts-tab post: a thread died with
    # the uploader when it restarted 6 s after the 11-02 stream (11-03).
    import subprocess
    import sys

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as log:
        subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "utils.ig_story",
             thumbnail_path, title or ""],
            stdout=log, stderr=subprocess.STDOUT, cwd=_APP,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    print("[IG Story] the new-stream Story goes up in a minute "
          "(logs/ig_story.log).")


def run_once(thumbnail_path: str, title: str, posting: dict) -> bool:
    folder = tempfile.mkdtemp(prefix="ig_story_")
    out = os.path.join(folder, "story.mp4")
    try:
        return bool(render_story(thumbnail_path, title, out)
                    and post_story(posting, out, {"posting": posting}))
    except Exception as exc:
        print(f"[IG Story] failed: {exc}")
        return False
    finally:
        for name in ("story.mp4", "story_title.txt"):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
        try:
            os.rmdir(folder)
        except OSError:
            pass


_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(_APP, "logs", "ig_story.log")


if __name__ == "__main__":
    import sys
    import time

    sys.path.insert(0, _APP)
    os.chdir(_APP)
    from utils.config import load_config

    print(f"{time.strftime('%m-%d %H:%M:%S')} story for {sys.argv[2]!r}",
          flush=True)
    app_cfg = load_config("config.json")
    run_once(sys.argv[1], sys.argv[2], app_cfg.posting or {})

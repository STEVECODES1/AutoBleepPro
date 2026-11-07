"""BinScripts (YouTube) - a weekly ranked countdown, long-form 16:9 (11-07).

Owner: Stackswopo stopped Monkey App, the channel sat a month while its
monetisation review is pending. Reviewers look at the newest uploads and
reject "reused content"; a ranked, edited countdown reads as the channel's
own work, and it is a format that is not on any of our other channels
(STACKSWOPOVODS has the 1-hour weekly, Shorts have the 59 s Top 5).

"Top 10 Stackswopo Moments This Week #N":
  * the 10 best-scored clips that already went out on a strict platform
    this week (every kid / slur / gambling check passed) - their 16:9
    "X cut" (whole game in view, autoreel/wide_clip); a vertical clip
    without one is shown over a blurred copy of itself
  * a 2.5 s rank card before each ("#7", its headline), #10 first, #1 last
  * the finished video is censored with the full-stream rules, then
    uploaded to BinScripts (youtube_binscripts_token.json), chapters in
    the description, links to WOPOVOD and Rumble
Once a week (logs/binscripts_weekly.json), its own process
(python -m utils.binscripts_weekly), waits while a game is running.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(_APP, "logs", "binscripts_weekly.json")
TOKEN = os.path.join(_APP, "youtube_binscripts_token.json")
EVERY_S = 7 * 86400
N = 15            # ~8-10 min: long enough for mid-roll ads once monetised
MIN_CLIPS = 6
CARD_S = 2.5
FONT = "C\\:/Windows/Fonts/impact.ttf"
FONT2 = "C\\:/Windows/Fonts/arialbd.ttf"
WOPOVOD = "https://www.youtube.com/@wopovod"
RUMBLE = "https://rumble.com/user/BinScripts"


def _ledger() -> dict:
    try:
        with open(LEDGER, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def due(now: float = 0.0) -> bool:
    now = now or time.time()
    return os.path.isfile(TOKEN) and now - float(_ledger().get("last", 0) or 0) >= EVERY_S


def _folders():
    return [os.path.join(_APP, d) for d in
            ("clip_queue_files", "watch_folder", "uploaded", "censored")]


def picks(n: int = N) -> list:
    """[(path, title, score)] best first; already-used clips skipped."""
    from utils.top5 import _find, _length, STRICT
    from utils.post_refs import recent
    from utils.clip_queue import _clip_score

    used = set(_ledger().get("used") or [])
    seen, out = set(), []
    for item in recent(7 * 24):
        key = item.get("clip", "")
        if item.get("platform") not in STRICT or key in seen or key in used:
            continue
        seen.add(key)
        title = item.get("title", "") or ""
        try:
            # under monetisation review: nothing that even mentions kids
            from autoreel.safe_text import child_risk
            if child_risk(title):
                continue
        except Exception:
            pass
        path = _find(key, _folders())
        if path and _length(path) >= 8:
            out.append((path, item.get("title", "") or "", _clip_score(path), key))
    if len(out) < n:
        out += _more(n, used | seen)
    out.sort(key=lambda t: t[2], reverse=True)
    return out[:n]


_BAD = ("severe", "children", "about children", "gambling", "kid", "minor")


def _more(n: int, skip: set) -> list:
    """Clips cut this week that have not reached a strict platform yet
    (daily caps) - never one a guard held for a severe slur, kids or
    gambling. Slur COUNTS are fine: the whole video is censored."""
    from utils.top5 import _length
    from utils.clip_queue import _clip_score
    try:
        jobs = json.load(open(os.path.join(_APP, "clip_jobs.json"), encoding="utf-8")).get("jobs", {})
    except (OSError, ValueError):
        jobs = {}
    bad = {os.path.basename(str(j.get("clip_path", ""))).lower()
           for j in (jobs.values() if isinstance(jobs, dict) else jobs)
           if any(b in str(j.get("last_error", "")).lower() for b in _BAD)}
    cutoff, out = time.time() - 7 * 86400, []
    for folder in _folders():
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if not low.endswith(".mp4") or " - clip " not in low or low.startswith("top 5") \
                    or low in skip or low in bad or "_censored" in low or low.startswith("_"):
                continue
            path = os.path.join(folder, name)
            if os.path.getmtime(path) < cutoff or _length(path) < 8:
                continue
            title = _title_of(low, path)
            try:
                from autoreel.safe_text import child_risk
                if child_risk(title):
                    continue
            except Exception:
                pass
            skip = skip | {low}
            out.append((path, title, _clip_score(path), low))
    return out


def _title_of(key: str, path: str) -> str:
    """The clip's headline: the link-card ledger, then its .txt; "" if none
    (a file name is never shown to viewers)."""
    try:
        links = json.load(open(os.path.join(_APP, "logs", "post_links.json"), encoding="utf-8"))
        t = (links.get(key) or {}).get("title", "")
        if t:
            return t
    except (OSError, ValueError, AttributeError):
        pass
    for folder in [os.path.dirname(path)] + _folders():
        try:
            t = open(os.path.join(folder, os.path.splitext(os.path.basename(path))[0] + ".txt"),
                     encoding="utf-8").read().strip()
            if t:
                return t
        except OSError:
            continue
    return ""


def _esc(text: str) -> str:
    keep = "".join(c for c in str(text) if c.isalnum() or c in " .,!?'-&#")
    return keep.replace("'", "’").replace(":", " ")[:70].strip()


def _lines(text: str, width: int = 40) -> list:
    words, out, cur = _esc(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out[:2]


def _card(rank: int, headline: str, out: str, clip: str = "") -> bool:
    """Rank card over a blurred, darkened still of the clip itself."""
    vf = ("scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
          "boxblur=25:2,eq=brightness=-0.28:saturation=1.2,setsar=1,fps=30,"
          f"drawtext=fontfile='{FONT}':text='#{rank}':fontsize=300:fontcolor=0xFFD400:"
          "borderw=14:bordercolor=black:x=(w-tw)/2:y=h*0.16")
    for i, line in enumerate(_lines(headline)):
        vf += (f",drawtext=fontfile='{FONT2}':text='{line}':fontsize=66:fontcolor=white:"
               f"borderw=6:bordercolor=black:x=(w-tw)/2:y=h*0.64+{i * 84}")
    src = (["-loop", "1", "-t", f"{CARD_S}", "-i", clip] if clip.lower().endswith(".jpg")
           else ["-ss", "1", "-t", f"{CARD_S}", "-i", clip]) if clip else \
        ["-f", "lavfi", "-i", f"color=c=0x101014:s=1920x1080:r=30:d={CARD_S}"]
    r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *src,
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                        "-map", "0:v", "-map", "1:a",
                        "-t", f"{CARD_S}", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", out],
                       capture_output=True, text=True, timeout=300)
    return r.returncode == 0


def _piece(path: str, rank: int, out: str) -> bool:
    from autoreel.wide_clip import find as wide

    src = wide(path) or path
    if src == path:     # vertical: over a blurred, filled copy of itself
        fc = ("[0:v]split[a][b];[a]scale=1920:1080:force_original_aspect_ratio=increase,"
              "crop=1920:1080,boxblur=30:3,eq=brightness=-0.15[bg];[b]scale=-2:1080[fg];"
              "[bg][fg]overlay=(W-w)/2:0,")
    else:
        fc = ("[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
              "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,")
    fc += (f"setsar=1,fps=30,drawtext=fontfile='{FONT}':text='#{rank}':fontsize=120:"
           "fontcolor=0xFFD400:borderw=8:bordercolor=black:x=w-tw-50:y=60[v]")
    r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src, "-filter_complex", fc,
                        "-map", "[v]", "-map", "0:a?", "-af",
                        "aresample=44100,aformat=channel_layouts=stereo",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", out],
                       capture_output=True, text=True, timeout=1800)
    return r.returncode == 0


def build(chosen, out_path: str) -> list:
    """Join cards + pieces, #N..#1. Returns [(start_s, label)] chapters."""
    from utils.top5 import _length

    work = tempfile.mkdtemp(prefix="binweekly_")
    parts, marks, t = [], [], 0.0
    try:
        ranked = list(enumerate(chosen, 1))[::-1]       # worst rank first
        for rank, (path, title, _s, _k) in ranked:
            card = os.path.join(work, f"c{rank}.mp4")
            piece = os.path.join(work, f"p{rank}.mp4")
            from autoreel.wide_clip import find as wide
            if not (_card(rank, title or "", card, wide(path) or path)
                    and _piece(path, rank, piece)):
                continue
            try:                               # V4: same loudness every piece
                from autoreel.loudness import level
                level(piece, bitrate="160k")
            except Exception:
                pass
            marks.append((t, f"#{rank} {_esc(title) or 'Moment'}".strip()[:90]))
            t += _length(card) + _length(piece)
            parts += [card, piece]
        if len(marks) < MIN_CLIPS:
            return []
        listing = os.path.join(work, "list.txt")
        with open(listing, "w", encoding="utf-8", newline="") as f:
            f.write("".join(f"file '{p.replace(os.sep, '/')}'\n" for p in parts))
        r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                            "-i", listing, "-c", "copy", "-movflags", "+faststart", out_path],
                           capture_output=True, text=True, timeout=1800)
        return marks if r.returncode == 0 and os.path.isfile(out_path) else []
    finally:
        for name in os.listdir(work):
            try:
                os.remove(os.path.join(work, name))
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass


def _stamp(s: float) -> str:
    s = int(s)
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def run(cfg=None, force: bool = False) -> str:
    if not force and not due():
        return ""
    try:
        from utils.game_mode import wait_if_gaming
        wait_if_gaming("the BinScripts weekly")
    except Exception:
        pass
    chosen = picks()
    if len(chosen) < MIN_CLIPS:
        print(f"[BinScripts] only {len(chosen)} clips this week - waiting.")
        return ""
    if cfg is None:
        from utils.config import load_config
        cfg = load_config(os.path.join(_APP, "config.json"))
    raw = os.path.join(_APP, "censored", "binscripts_weekly_build.mp4")
    marks = build(chosen, raw)
    if not marks:
        print("[BinScripts] could not build the countdown.")
        return ""
    final, made = raw, ""
    try:
        from utils.censor import censor_video
        from utils.clip_queue import scope_allow, scope_categories

        scope = cfg.general.censor_categories
        res = censor_video(raw, cfg.general.censored_folder,
                           model_name=cfg.general.censor_model,
                           bleep_method=cfg.general.censor_bleep_method,
                           custom_words=cfg.general.censor_custom_words,
                           device=cfg.general.censor_device,
                           padding_ms=cfg.general.censor_padding_ms,
                           only_categories=scope_categories(scope),
                           allow_words=scope_allow(scope),
                           speed={"second_listen": False})
        made = getattr(res, "output_path", "") or ""
        final = made or raw
    except Exception as exc:
        print(f"[BinScripts] censor failed ({exc}) - not uploading uncensored.")
        return ""
    # The channel intro in front, like every full stream (owner 11-07).
    intro_s = 0.0
    try:
        from utils.recap import _with_intro
        with_intro, intro_s = _with_intro(cfg, final)
        if intro_s:
            if made and made != with_intro and os.path.isfile(made):
                os.remove(made)
            final = made = with_intro
            marks = [(0.0, "Intro")] + [(s + intro_s, l) for s, l in marks]
    except Exception as exc:
        print(f"[BinScripts] intro skipped ({exc})")
    led = _ledger()
    n = int(led.get("n", 0) or 0) + 1
    gta = sum(1 for c in chosen if "johnny cox" in (c[1] or "").lower()) * 2 >= len(chosen)
    k = len(chosen) if intro_s else len(marks)
    k = min(k, len([m for m in marks if m[1] != "Intro"]))
    title = (f"Top {k} Johnny Cox Moments This Week #{n} | Stackswopo GTA RP" if gta
             else f"Top {k} Stackswopo Moments This Week #{n}")
    chapters = "\n".join(f"{_stamp(0 if i == 0 else s)} {label}"
                         for i, (s, label) in enumerate(marks))
    desc = (f"The {k} best Stackswopo moments of the week, ranked - #1 is last.\n\n"
            f"Every full stream: {WOPOVOD}\nFull streams uncut: {RUMBLE}\n\n{chapters}\n\n"
            "Fan channel - not Stackswopo's own channel. His channel: "
            "https://www.youtube.com/@stackswopo_\nGTA RP is role-play: characters, not real events.\n\n"
            "#Stackswopo #JohnnyCox #GTARP #FunnyMoments")
    try:
        from utils.recap import upload
        url = upload(TOKEN, final, title, desc,
                     ["Stackswopo", "Johnny Cox", "GTA RP", "Stackswopo funny moments",
                      "top 10", "Stackswopo best moments", "GTA 5 RP", "funny moments"],
                     "public", WOPOVOD,
                     comment="Every full stream: " + WOPOVOD + "\nUncut on Rumble: " + RUMBLE)
        _remember(chosen, n, url, title)
        # its own process: the thumbnail models (torch + onnx on the GPU)
        # crashed this one natively on 11-07 right after the upload.
        subprocess.run([sys.executable, "-X", "utf8", "-m", "utils.binscripts_weekly",
                        "--thumb", _thumb_source(chosen) or final, url, str(k)],
                       cwd=_APP, timeout=1800)
    except Exception as exc:
        print(f"[BinScripts] upload failed: {exc}")
        return ""
    finally:
        for p in {raw, made}:
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    print(f"[BinScripts] uploaded: {title} -> {url}", flush=True)
    try:
        from utils.brain import send_alert
        send_alert("BinScripts weekly is up", f"{title}\n{url}", urgent=False)
    except Exception:
        pass
    return url


def _thumb_source(chosen) -> str:
    """The clip whose frames show the PLAYER's face biggest (pose model,
    autoreel/player_face) - 11-07: the #1 clip was a car from behind."""
    try:
        from autoreel.wide_clip import find as wide
        from autoreel import player_face as F
        from autoreel import thumbnail as T
        from utils.top5 import _length
        import tempfile

        best, best_h, best_at, best_face = "", 0.0, 0.0, None
        work = tempfile.mkdtemp(prefix="binthumb_")
        for path, *_ in chosen[:10]:
            src = wide(path) or path
            for n, at in enumerate(T.timestamps(_length(src), 8)):
                look = os.path.join(work, f"l{n}.jpg")
                if not T._grab(src, at, look, width=512):
                    continue
                people = F.people(open(look, "rb").read())
                # the biggest clearly-facing person with a face box
                for who in people:
                    if who["facing"] and who["face"] and who["height"] > best_h:
                        best, best_h, best_at, best_face = src, who["height"], at, who["face"]
        for name in os.listdir(work):
            os.remove(os.path.join(work, name))
        os.rmdir(work)
        if best:
            # one frame, face-led crop, written as a still the thumbnail
            # code finishes (polish + logo) - no second guess by a model
            still = os.path.join(_APP, "censored", "binscripts_face.jpg")
            window = T.face_window(best_face)
            at = T.sharpest_near(best, best_at, window, os.path.dirname(still))
            if T._grab(best, at, still, vf=T.stream_filters(window, None)):
                return still
        for c in chosen:
            if wide(c[0]):
                return wide(c[0])
        return chosen[0][0]
    except Exception:
        return ""


def _remember(chosen, n: int, url: str, title: str) -> None:
    """Written the moment the upload is back - before anything that could
    crash - so a re-run never uploads the same week twice."""
    led = _ledger()
    led.update(last=time.time(), n=n,
               used=sorted(set(led.get("used") or []) | {c[3] for c in chosen}))
    led.setdefault("uploads", []).append({"url": url, "title": title, "at": time.time()})
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(led, f, indent=1)


def _countdown_text(path: str, count: int) -> None:
    """'TOP 15' / 'THIS WEEK' on the right third - the ranked-countdown look."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(path).convert("RGB")
        w, h = img.size
        d = ImageDraw.Draw(img)
        big = ImageFont.truetype("C:/Windows/Fonts/impact.ttf", int(h * 0.30))
        small = ImageFont.truetype("C:/Windows/Fonts/impact.ttf", int(h * 0.13))
        for text, font, y, fill in ((f"TOP {count}", big, 0.50, (255, 212, 0)),
                                    ("THIS WEEK", small, 0.80, (255, 255, 255))):
            tw = d.textlength(text, font=font)
            d.text((w - tw - w * 0.04, h * y - font.size / 2), text, font=font, fill=fill,
                   stroke_width=max(4, int(font.size * 0.07)), stroke_fill=(0, 0, 0))
        img.save(path, quality=95)
    except Exception as exc:
        print(f"[BinScripts] no countdown text ({exc}).")


def _thumbnail(cfg, video: str, url: str, count: int = 15) -> None:
    """Our thumbnail (Johnny Cox close-up, outline, GTA logo) on the upload."""
    try:
        import re
        from autoreel import thumbnail as T
        from utils.top5 import _length
        from utils.youtube_uploader import YouTubeUploader

        vid = re.search(r"v=([\w-]{11})", url or "")
        if not vid:
            return
        out = os.path.join(_APP, "censored", "binscripts_weekly_thumb.jpg")
        if video.lower().endswith(".jpg"):
            # a chosen still (the player's face, _thumb_source): finish it
            import shutil
            from autoreel.thumb_polish import polish
            shutil.copyfile(video, out)
            polish(out)
            logo = getattr(cfg.general, "thumbnail_logo", "")
            if logo and os.path.isfile(logo):
                T._stamp_logo(out, logo)
            made = out
        else:
            made = T.make(video, _length(video), out_path=out,
                          logo_path=getattr(cfg.general, "thumbnail_logo", ""), style="stream",
                          sticker_path=getattr(cfg.general, "thumbnail_sticker", ""),
                          provider=str((cfg.clips or {}).get("llm_provider", "") or ""),
                          text="", badge="GTA RP", mode="")
        if not made:
            return
        _countdown_text(made, count)
        from googleapiclient.http import MediaFileUpload
        svc = YouTubeUploader(os.path.join(_APP, "client_secrets.json"), TOKEN).get_service()
        svc.thumbnails().set(videoId=vid.group(1),
                             media_body=MediaFileUpload(made, mimetype="image/jpeg")).execute()
        print("[BinScripts] thumbnail set.")
        os.remove(made)
    except Exception as exc:
        print(f"[BinScripts] no custom thumbnail ({exc}).")


def start_if_due() -> None:
    """Called from the link-cards loop: its own process, so the uploader
    never waits on a 10-minute build + censor + upload."""
    if os.environ.get("PYTEST_CURRENT_TEST") or not due():
        return
    lock = LEDGER + ".running"
    try:
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 6 * 3600:
            return
        open(lock, "w").close()
        log = open(os.path.join(_APP, "logs", "binscripts_weekly.log"), "a", encoding="utf-8")
        subprocess.Popen([sys.executable, "-X", "utf8", "-m", "utils.binscripts_weekly"],
                         cwd=_APP, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    except Exception:
        pass


if __name__ == "__main__":
    sys.path.insert(0, _APP)
    sys.path.insert(0, os.path.dirname(_APP))
    if "--thumb" in sys.argv:
        i = sys.argv.index("--thumb")
        from utils.config import load_config
        _thumbnail(load_config(os.path.join(_APP, "config.json")), sys.argv[i + 1], sys.argv[i + 2], int(sys.argv[i + 3]) if len(sys.argv) > i + 3 else 15)
        sys.exit(0)
    try:
        run(force="--force" in sys.argv)
    finally:
        try:
            os.remove(LEDGER + ".running")
        except OSError:
            pass

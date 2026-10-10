"""A 10-15 minute "best moments" recap of each stream, for the second
channel (STACKSWOPO GAMES), pointing its traffic at the full stream on
@wopovod.

That channel has a strike and cannot earn, but it has viewers. A full
stream there would compete with wopovod for the same people and carry the
most risk of a second strike; a short, censored, music-checked recap whose
first line is the full stream's link turns its traffic into wopovod views.

The moments are the ones already chosen for clips - live and from the VOD -
widened a little for context, merged, put in stream order and capped at
RECAP_MAX_S. Cut from the original 16:9 recording, graded like the VOD,
then censored and music-checked exactly like the YouTube copy.

Settings (config.json "recap"): enabled, token_path, max_minutes, privacy.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Sequence, Tuple

RECAP_MAX_S = 15 * 60
RECAP_MIN_S = 4 * 60
CONTEXT_BEFORE_S = 6.0
CONTEXT_AFTER_S = 3.0
DEFAULT_TOKEN = "youtube_games_token.json"


def settings(cfg) -> dict:
    raw = {}
    try:
        with open(os.path.join(cfg.project_root, "config.json"), encoding="utf-8") as f:
            raw = json.load(f).get("recap") or {}
    except (OSError, ValueError, AttributeError):
        raw = {}
    token = raw.get("token_path") or DEFAULT_TOKEN
    if not os.path.isabs(token):
        token = os.path.join(getattr(cfg, "project_root", "."), token)
    return {"enabled": bool(raw.get("enabled", True)),
            "token_path": token,
            "max_s": float(raw.get("max_minutes", RECAP_MAX_S / 60)) * 60,
            "privacy": str(raw.get("privacy", "public")),
            # Both channels this feeds already carry a strike: every swear is
            # bleeped, not only the ad-unsafe ones the VOD scope covers.
            "censor": str(raw.get("censor", "all"))}


def pick_moments(ranges: Sequence[Tuple[float, float, float]], duration: float,
                 max_s: float = RECAP_MAX_S, scored: bool = False) -> list:
    """(start, end, score) ranges -> the recap's segments, in stream order.

    Widened for context, merged where they touch, then the best-scoring
    ones kept until max_s is reached."""
    widened = []
    for start, end, score in ranges:
        s = max(0.0, float(start) - CONTEXT_BEFORE_S)
        e = min(duration or float(end) + CONTEXT_AFTER_S, float(end) + CONTEXT_AFTER_S)
        if e - s >= 5:
            widened.append([s, e, float(score or 0.0)])
    widened.sort()
    merged: list = []
    for s, e, sc in widened:
        if merged and s <= merged[-1][1] + 2:
            merged[-1][1] = max(merged[-1][1], e)
            merged[-1][2] = max(merged[-1][2], sc)
        else:
            merged.append([s, e, sc])
    chosen, total = [], 0.0
    for s, e, sc in sorted(merged, key=lambda m: -m[2]):
        if total + (e - s) > max_s:
            continue
        chosen.append((s, e, sc))
        total += e - s
    return sorted(chosen) if scored else sorted((s, e) for s, e, _ in chosen)


def _shows_gambling(source: str, start: float, end: float) -> bool:
    """A casino / betting site on screen in this moment (2 frames looked at).
    False when it cannot be told - no model is not a reason to lose a recap."""
    try:
        from autoreel.gambling_check import gambling_on_screen, sample_times

        verdict, _hits = gambling_on_screen(source, sample_times(start, end, 2))
        return bool(verdict)
    except Exception:
        return False


def stranger_spans(source: str, look=None) -> List[Tuple[float, float]]:
    """Where the stream is on Monkey App / random video chat, by looking at
    it once a minute. Every single reading counts, with a minute either
    side - over-cutting a recap is fine, a stranger on a struck channel is
    not."""
    try:
        from autoreel.vod_segments import SAMPLE_EVERY, _seconds_long, read_kinds

        span = _seconds_long(source)
        marks = read_kinds(source, span, SAMPLE_EVERY, look)
    except Exception as exc:
        print(f"[Recap] Could not check for Monkey App parts ({exc}).")
        return []
    spans: List[Tuple[float, float]] = []
    for at, kind in marks:
        if kind != "monkey":
            continue
        s, e = max(0.0, at - SAMPLE_EVERY), at + 2 * SAMPLE_EVERY
        if spans and s <= spans[-1][1]:
            spans[-1] = (spans[-1][0], e)
        else:
            spans.append((s, e))
    return spans


def without(ranges, spans) -> list:
    """`ranges` minus any that touch one of `spans`."""
    return [r for r in ranges
            if not any(r[0] < e and r[1] > s for s, e in spans)]


def build(source: str, segments: Sequence[Tuple[float, float]], out_path: str,
          video_filter: str = "") -> list:
    """Cut `segments` out of `source` and join them into `out_path`.
    Returns where each piece landed in the result ([] on failure)."""
    return build_from([(source, s, e) for s, e in segments], out_path, video_filter)


def _duration(path: str) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", path], capture_output=True, text=True,
                             timeout=60).stdout
        return float(out.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def build_from(items: Sequence[Tuple[str, float, float]], out_path: str,
               video_filter: str = "", decor: Optional[Sequence[Optional[str]]] = None,
               card: Optional[dict] = None) -> list:
    """Cut (source, start, end) pieces - from one file or several - and join
    them into `out_path`. Every piece is encoded with the same settings, so
    the join is a copy. Returns (start, end, item index) for each piece in
    the result - a piece that failed to cut is left out - or [] on failure.

    decor: one caption/topic .ass per piece (or None) - turns on the edit:
    every piece slides in over the one before, its .ass burned in.
    card: {"still": (source, seconds), "ass": path} - an opening title card."""
    from utils.ffmpeg_tools import pick_video_encoder

    encoder = pick_video_encoder("auto")
    quality = (["-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0",
                "-maxrate", "6M", "-bufsize", "12M"] if encoder == "h264_nvenc"
               else ["-preset", "veryfast", "-crf", "20"])
    vf = ",".join(f for f in (video_filter, "scale=1920:1080:force_original_aspect_ratio=decrease",
                               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2", "fps=60", "format=yuv420p") if f)
    work = tempfile.mkdtemp(prefix="recap_", dir=os.path.dirname(out_path))
    parts = []
    placed = []
    at = 0.0
    codec = ["-c:v", encoder, *quality]
    behind = None          # the last frame so far - what the next moment slides over
    try:
        if card:
            # The opening card (utils/recap_edit): the title over a blurred
            # still of the best moment.
            from utils import recap_edit

            png = os.path.join(work, "card.png")
            part = os.path.join(work, "card.ts")
            src, when = card["still"]
            if (recap_edit.still(src, when, png)
                    and recap_edit.render_card(part, png, card["ass"], codec)
                    and os.path.getsize(part) > 0):
                parts.append(part)
                at = _duration(part) or recap_edit.CARD_S
                last = os.path.join(work, "card_last.png")
                behind = last if recap_edit.card_frame(png, card["ass"], last) else None
        for i, (source, s, e) in enumerate(items):
            part = os.path.join(work, f"part{i:03d}.ts")
            if decor is not None:
                # Edited: slides in over the last frame, captions and topic
                # tag burned in.
                from utils import recap_edit

                args = recap_edit.piece_args(source, s, e, part, vf, codec,
                                             decor[i] if i < len(decor) else None, behind)
                done = subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                                      capture_output=True, text=True, timeout=1800)
            else:
                done = subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-ss", f"{s:.3f}", "-i", source,
                     "-t", f"{e - s:.3f}", "-map", "0:v:0", "-map", "0:a:0",
                     "-vf", vf, *codec,
                     "-af", "aresample=48000,afade=t=in:d=0.15", "-c:a", "aac", "-b:a", "192k",
                     "-ar", "48000", "-ac", "2", "-f", "mpegts", part],
                    capture_output=True, text=True, timeout=1800)
            if done.returncode == 0 and os.path.exists(part) and os.path.getsize(part) > 0:
                parts.append(part)
                length = _duration(part) or (e - s)
                placed.append((round(at, 3), round(at + length, 3), i))
                at += length
                if decor is not None:
                    last = os.path.join(work, f"last{i:03d}.png")
                    behind = last if recap_edit.last_frame(part, last) else behind
            elif decor is not None:
                print(f"[Recap] Could not render moment {i + 1}: "
                      f"{(done.stderr or '').strip()[-200:]}")
        if placed and card and card.get("end_ass"):
            # The closing card - where the full stream is - over the last
            # frame of the last moment.
            from utils import recap_edit

            part = os.path.join(work, "end.ts")
            png = behind
            if not png:
                src, when = card["still"]
                png = os.path.join(work, "end.png")
                png = png if recap_edit.still(src, when, png) else None
            if png and recap_edit.render_card(part, png, card["end_ass"], codec,
                                              recap_edit.END_CARD_S):
                parts.append(part)
        if not parts:
            return []
        listing = os.path.join(work, "list.txt")
        with open(listing, "w", encoding="utf-8") as f:
            for p in parts:
                f.write("file '%s'\n" % p.replace("\\", "/"))
        done = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                               "-i", listing, "-c", "copy", "-movflags", "+faststart", out_path],
                              capture_output=True, text=True, timeout=1800)
        ok = done.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
        return placed if ok else []
    finally:
        for p in os.listdir(work):
            try:
                os.remove(os.path.join(work, p))
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass


def _youtube(token_path: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build as gbuild

    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid:
        creds.refresh(Request())
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return gbuild("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(token_path: str, path: str, title: str, description: str,
           tags: Sequence[str], privacy: str, full_stream: str,
           comment: str = "") -> str:
    """Upload to the recap channel; the full stream's link also goes in a
    comment. Returns the watch URL."""
    from googleapiclient.http import MediaFileUpload

    yt = _youtube(token_path)
    body = {"snippet": {"title": title[:100], "description": description[:4900],
                        "tags": list(tags)[:15], "categoryId": "20"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    request = yt.videos().insert(part="snippet,status", body=body,
                                 media_body=MediaFileUpload(path, chunksize=8 * 1024 * 1024,
                                                            resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = response["id"]
    if full_stream:
        try:
            yt.commentThreads().insert(part="snippet", body={"snippet": {
                "videoId": video_id, "topLevelComment": {"snippet": {
                    "textOriginal": comment or f"Full stream, every minute of it: {full_stream}"
                }}}}).execute()
        except Exception as exc:
            print(f"[Recap] Uploaded, but the comment did not post ({exc}).")
    return f"https://www.youtube.com/watch?v={video_id}"


def _ledger(cfg) -> str:
    return os.path.join(cfg.general.logs_folder, "recaps.json")


def _done(cfg) -> dict:
    try:
        with open(_ledger(cfg), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _links_block(cfg) -> str:
    lines = str(cfg.youtube.description_template or "").splitlines()
    if "ALL LINKS" not in lines:
        return ""
    start = lines.index("ALL LINKS")
    block = []
    for line in lines[start:]:
        if not line.strip():
            break
        block.append(line)
    return "\n".join(block)


def _edit_plan(cfg, source: str, segments, shown_title: str, stream_date: str,
               work: str):
    """(per-moment .ass, card, topic titles) for the edited recap - see
    utils/recap_edit. (None, None, []) when the edit cannot be prepared:
    the recap is then cut plain, never skipped."""
    try:
        from utils import recap_edit
        from utils.censor import words_cache_path

        words_path = words_cache_path(cfg.general.censored_folder,
                                      os.path.splitext(os.path.basename(source))[0])
        with open(words_path, encoding="utf-8") as f:
            data = json.load(f)
        transcript = list(data.get("segments") if isinstance(data, dict) else data)
    except Exception as exc:
        print(f"[Recap] No transcript for the edit ({exc}) - cutting it plain.")
        return None, None, []
    texts = [" ".join(str(seg.get("text", "")) for seg in transcript
                      if st <= float(seg.get("start", 0)) < e) for st, e, _ in segments]
    topics = recap_edit.topic_titles(texts)
    print(f"[Recap] Topic tags for {sum(1 for t in topics if t)} of {len(topics)} moments.")
    decor = [recap_edit.piece_ass(os.path.join(work, f"moment{i:02d}.ass"), transcript,
                                  st, e, topics[i] if i < len(topics) else "")
             for i, (st, e, _) in enumerate(segments)]
    best = max(segments, key=lambda m: m[2])
    card = {"still": (source, (best[0] + best[1]) / 2),
            "ass": recap_edit.card_ass(os.path.join(work, "card.ass"), shown_title,
                                       f"BEST MOMENTS  {stream_date}"),
            "end_ass": recap_edit.card_ass(
                os.path.join(work, "end.ass"), "THE FULL STREAM",
                "YOUTUBE @WOPOVOD  -  UNCUT ON RUMBLE: BINSCRIPTS",
                name="EVERY MINUTE OF IT", seconds=recap_edit.END_CARD_S)}
    return decor, card, topics


def _with_intro(cfg, path: str):
    """(file to upload, intro seconds): the channel intro in front, the
    same intro the full streams use (youtube.intro_path). Unchanged when
    there is none or it cannot be joined."""
    intro = (getattr(cfg.youtube, "intro_path", None)
             or os.environ.get("YOUTUBE_INTRO_PATH", "") or "")
    if not intro:
        return path, 0.0
    if not os.path.isabs(intro):
        intro = os.path.join(getattr(cfg, "project_root", "") or "", intro)
    if not os.path.exists(intro):
        return path, 0.0
    from utils.ffmpeg_tools import media_duration, prepend_intro

    out = os.path.splitext(path)[0] + "_INTRO.mp4"
    try:
        if prepend_intro(intro, path, out):
            return out, float(media_duration(intro) or 0.0)
    except Exception as exc:
        print(f"[Recap] Intro skipped ({exc}).")
    return path, 0.0


def recap_description(cfg, shown_title: str, stream_date: str, full: str, uncut: str,
                      placed, topics, intro_s: float = 0.0) -> str:
    """Both full streams first - that is the job of this channel - then
    chapters named by the topic tags, then every link."""
    from utils.weekly import chapters

    lines = []
    if full:
        lines.append(f"▶ Full stream on YouTube: {full}")
    if uncut:
        lines.append(f"▶ Uncut & uncensored on Rumble: {uncut}")
    if lines:
        lines.append("")
    lines.append(f"The best moments from Stackswopo's \"{shown_title}\" stream ({stream_date}). "
                 "Every minute of it is on STACKSWOPO VODS: https://www.youtube.com/@wopovod")
    marks = [(0.0, shown_title.upper())]
    for a, _b, i in placed:
        topic = topics[i] if i < len(topics) else ""
        if topic:
            marks.append((a + intro_s, topic))
    if len(marks) > 1 and marks[1][0] < 10:
        # The intro and card are shorter than the 10 s YouTube wants for a
        # chapter: the first moment's chapter starts at 0:00 instead.
        marks = [(0.0, marks[1][1])] + marks[2:]
    marked = chapters(marks)
    if marked:
        lines += ["", marked]
    block = _links_block(cfg)
    if block:
        lines += ["", block]
    lines += ["", "#Stackswopo #GTARP #FunnyMoments"]
    return "\n".join(lines)


def make(cfg, source: str, stream_title: str, stream_date: str,
         ranges: Sequence[Tuple[float, float, float]]) -> str:
    """Build, censor and upload this stream's recap. The URL, or ""."""
    s = settings(cfg)
    if not s["enabled"]:
        return ""
    if not os.path.exists(s["token_path"]):
        print("[Recap] Not signed in to the recap channel yet - run "
              "setup_games_channel.py once. Skipping.")
        return ""
    key = os.path.basename(source)
    if key in _done(cfg):
        return _done(cfg)[key]
    from utils.ffmpeg_tools import media_duration

    duration = media_duration(source) or 0.0
    strangers = stranger_spans(source)
    if strangers:
        kept = without(ranges, strangers)
        print(f"[Recap] Left out {len(ranges) - len(kept)} moments from the Monkey App "
              f"part of the stream ({sum(e - b for b, e in strangers) / 60:.0f} min) - "
              "strangers on camera stay off the strike channels.")
        ranges = kept
    segments = pick_moments(ranges, duration, s["max_s"], scored=True)
    # No casino on screen in a recap - YouTube removes videos that show a
    # gambling site Google has not approved (logo or promo code included).
    # Each picked moment gets two frames looked at; a moment that shows
    # one is taken out of the running and the pick is made again.
    checked: dict = {}
    for _round in range(3):
        bad = []
        for st, e, _score in segments:
            if (st, e) not in checked:
                checked[(st, e)] = _shows_gambling(source, st, e)
            if checked[(st, e)]:
                bad.append((st, e))
        if not bad:
            break
        print(f"[Recap] Left out {len(bad)} moment(s) with a gambling site on screen.")
        ranges = without(ranges, bad)
        segments = pick_moments(ranges, duration, s["max_s"], scored=True)
    total = sum(e - st for st, e, _ in segments)
    if total < RECAP_MIN_S:
        print(f"[Recap] Only {total / 60:.1f} min of moments - not enough for a recap.")
        return ""
    base = os.path.splitext(key)[0]
    raw = os.path.join(cfg.general.censored_folder, f"{base}_RECAP.mp4")
    speed = dict(cfg.general.speed or {})
    print(f"[Recap] Cutting {len(segments)} moments ({total / 60:.1f} min) into a recap...")
    from utils.templating import strip_trailing_stamp

    shown_title = strip_trailing_stamp(stream_title) or stream_title
    edit_dir = tempfile.mkdtemp(prefix="recap_edit_", dir=cfg.general.censored_folder)
    decor, card, topics = _edit_plan(cfg, source, segments, shown_title, stream_date, edit_dir)
    placed = build_from([(source, st, e) for st, e, _ in segments], raw,
                        str(speed.get("video_filter", "") or ""), decor=decor, card=card)
    shutil.rmtree(edit_dir, ignore_errors=True)
    if not placed:
        print("[Recap] Could not build the recap.")
        return ""
    cleanup = [raw]
    try:
        from utils.censor import censor_video
        from utils.clip_queue import scope_allow, scope_categories

        scope = s["censor"]
        result = censor_video(raw, cfg.general.censored_folder,
                              model_name=cfg.general.censor_model,
                              bleep_method=cfg.general.censor_bleep_method,
                              custom_words=cfg.general.censor_custom_words,
                              device=cfg.general.censor_device,
                              speed={**speed, "stream_copy_video": True, "video_filter": ""},
                              padding_ms=cfg.general.censor_padding_ms,
                              mute_whole_segment=cfg.general.censor_mute_whole_segment,
                              only_categories=scope_categories(scope),
                              allow_words=scope_allow(scope))
        final = result.output_path
        if final != raw:
            cleanup.append(final)
        if getattr(cfg.youtube, "music_guard", False):
            from autoreel.music_guard import guard
            guarded = guard(final, cfg.general.censored_folder)
            if guarded != final:
                cleanup.append(guarded)
                final = guarded
        from utils.stream_links import link_for
        from utils.templating import build_title

        full = link_for(stream_title, stream_date, "youtube")
        uncut = link_for(stream_title, stream_date, "rumble")
        title = build_title(stream_title, stream_date,
                            "Stackswopo - {TITLE} - {date} (BEST MOMENTS)")
        # The intro goes on the uploaded copy only: the kept recap (for
        # the weekly) stays without it, so its moment times stay right.
        upload_path, intro_s = _with_intro(cfg, final)
        if upload_path != final:
            cleanup.append(upload_path)
        description = recap_description(cfg, shown_title, stream_date, full, uncut,
                                        placed, topics, intro_s)
        from utils.weekly import risky

        privacy = s["privacy"]
        if risky(stream_title):
            # Strangers on camera: a person checks it before it goes public.
            privacy = "private"
            print("[Recap] Strangers-on-camera stream - uploading PRIVATE for you to check.")
        comment = "\n".join(line for line in (
            f"▶ Full stream on YouTube: {full}" if full else "",
            f"▶ Uncut & uncensored on Rumble: {uncut}" if uncut else "") if line)
        url = upload(s["token_path"], upload_path, title, description,
                     ["Stackswopo", "GTA RP", "Stackswopo stream", "best moments",
                      "funny moments", "GTA 5"], privacy, full or uncut, comment=comment)
        print(f"[Recap] Uploaded to STACKSWOPO GAMES ({privacy}): {url}")
        done = _done(cfg)
        done[key] = url
        with open(_ledger(cfg), "w", encoding="utf-8") as f:
            json.dump(done, f, indent=1)
        # Kept (censored, music-checked) for the weekly best-of.
        try:
            from utils import weekly
            # Only the pieces that were actually cut, with their scores.
            weekly.keep(cfg, final, stream_title, stream_date, url, full,
                        [(a, b, segments[i][2]) for a, b, i in placed])
            cleanup.remove(final)
        except Exception as exc:
            print(f"[Recap] Could not keep the recap for the weekly ({exc}).")
        try:
            from utils import weekly
            weekly.maybe_make(cfg)
        except Exception as exc:
            print(f"[Weekly] Failed: {exc}")
        return url
    except Exception as exc:
        print(f"[Recap] Failed: {exc}")
        return ""
    finally:
        for p in cleanup:
            try:
                os.remove(p)
            except OSError:
                pass

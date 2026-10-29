"""Story episodes for STACKSWOPOVODS (@stackswopo10k).

Owner, 10-29: "one of my channels should upload videos like
@innovativestizz" - a fan channel that takes ONE storyline out of a stream
(he answers the people trying to expose him, he plays Scam with friends,
he argues with his girlfriend, he reacts to a streamer's last stream),
cuts it as one continuous 15-50 minute episode and titles it like a story:
"Stackswopo Responds Back After Someone Tried To EXPOSE Him" - 13k-31k
views each, while the weekly best-of here got 37.

So after each stream's clips and recap, the whole transcript is read by
an AI (the free ones first, like the policy read) and asked for the 1-2
storylines a viewer would click: start, end, a third-person story title,
3-4 words for the thumbnail. The episode is that stretch of the stream,
continuous - with everything YouTube could strike taken out (kid talk,
the policy read's flags, the spoken patterns, Monkey App strangers, a
casino on screen), then censored like every YouTube upload, the intro on
front, a thumbnail with the words, and links to the full stream (wopovod)
and the uncut one (Rumble).

Settings (config.json "stories"): enabled (default true), per_stream
(default 1 - the 10,000-unit YouTube day is shared by all four channels),
min_minutes, max_minutes, privacy, token_path.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from typing import List, Optional, Sequence, Tuple

DEFAULT_TOKEN = "youtube_compilation_token.json"
MIN_S = 10 * 60
MAX_S = 45 * 60
MAX_CUT_SHARE = 0.35      # more of a story cut than this and it is choppy: dropped
PAD = 6.0

PROMPT = """You pick STORY EPISODES from a livestream by Stackswopo for a YouTube fan channel.
A story episode is ONE continuous stretch of the stream, {lo}-{hi} minutes long, about ONE
thing a viewer would click on: a beef or argument, him answering people who try to expose him,
him reacting to another streamer or drama, a game he plays with friends or chat (Scam, GTA RP
storylines, a challenge), a date or a call that goes wrong, a big announcement.
It must start where that story starts (not in the middle of something else) and end where it ends.
Good titles (third person, his name first, one or two CAPS words, a little mystery):
  Stackswopo Responds Back After Someone Tried To EXPOSE Him
  Stackswopo Plays Scam With Your Friends!
  Stackswopo REACTS to Reggie's LAST Twitch Stream
  Stackswopo ARGUES with his GIRLFRIEND on stream (She threatens to EXPOSE his face)
Never put slurs, sexual words, kids/ages or animal comparisons about people in a title.
Each line of the transcript starts with its time in seconds, like [1234].
Answer with JSON only, best first, at most {n}:
{{"stories": [{{"start": <seconds>, "end": <seconds>, "title": "<under 80 characters>",
  "thumb": "<2-4 punchy words for the thumbnail>", "why": "<under 15 words>"}}]}}
Never pick a stretch marked "(not usable ...)" - choose a story outside those parts.
If nothing in it is a real story, answer {{"stories": []}}.

Transcript:
"""


# ── settings / ledger ────────────────────────────────────────────────

def settings(cfg) -> dict:
    raw = {}
    try:
        with open(os.path.join(cfg.project_root, "config.json"), encoding="utf-8") as f:
            raw = json.load(f).get("stories") or {}
    except (OSError, ValueError, AttributeError):
        raw = {}
    token = raw.get("token_path") or DEFAULT_TOKEN
    if not os.path.isabs(token):
        token = os.path.join(getattr(cfg, "project_root", "."), token)
    return {"enabled": bool(raw.get("enabled", True)),
            "per_stream": max(0, int(raw.get("per_stream", 1))),
            "min_s": float(raw.get("min_minutes", MIN_S / 60)) * 60,
            "max_s": float(raw.get("max_minutes", MAX_S / 60)) * 60,
            "privacy": str(raw.get("privacy", "public")),
            "token_path": token}


def _ledger_path(cfg) -> str:
    return os.path.join(cfg.general.logs_folder, "stories.json")


def _ledger(cfg) -> dict:
    try:
        with open(_ledger_path(cfg), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(cfg, data: dict) -> None:
    os.makedirs(cfg.general.logs_folder, exist_ok=True)
    with open(_ledger_path(cfg), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


# ── picking the story ────────────────────────────────────────────────

def condensed(segments, every: float = 20.0, limit: int = 60000,
              blocked: Sequence[Tuple[float, float]] = ()) -> str:
    """The transcript as "[seconds] text" lines, one per ~20 s, short
    enough for a free model to read whole."""
    try:
        # Gemini refuses a transcript full of slurs ("stopped (SAFETY)",
        # 10-29): the words it will not touch come out, the story stays.
        from autoreel.llm_highlights import for_the_model as _clean
    except Exception:
        _clean = None
    lines, cur, start = [], [], None
    for s in segments or []:
        text = str(s.get("text") or "").strip()
        t0 = float(s.get("start") or 0)
        if text and any(x <= t0 < y for x, y in blocked):
            text = "(not usable - strangers on camera)"
        if _clean and text:
            try:
                text = _clean(text)
            except Exception:
                pass
        if not text:
            continue
        t = float(s.get("start") or 0)
        if start is None:
            start = t
        cur.append(text)
        if t - start >= every:
            lines.append(f"[{int(start)}] " + " ".join(cur))
            cur, start = [], None
    if cur:
        lines.append(f"[{int(start or 0)}] " + " ".join(cur))
    out = "\n".join(lines)
    if len(out) > limit:
        # Keep every line, shortened, rather than lose the end of the stream.
        keep = max(40, limit // max(1, len(lines)))
        out = "\n".join(l[:keep] for l in lines)
    return out


def parse(reply: str, duration: float, lo: float, hi: float) -> List[dict]:
    """The model's stories that make sense: inside the stream, the right
    length, not overlapping, with a title."""
    # The first JSON object in the reply - models add a stray "}" or
    # words after it (10-29: NVIDIA's answer ended "}]}}").
    text = reply or ""
    i = text.find("{")
    try:
        obj, _end = json.JSONDecoder().raw_decode(text[i:]) if i >= 0 else ({}, 0)
        raw = obj.get("stories") or []
    except (ValueError, AttributeError):
        return []
    out: List[dict] = []
    for s in raw:
        try:
            a, b = float(s["start"]), float(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        a, b = max(0.0, a), min(duration or b, b)
        title = " ".join(str(s.get("title") or "").split())
        if not title or b - a < lo * 0.9 or b - a > hi * 1.15:
            continue
        if any(a < o["end"] and b > o["start"] for o in out):
            continue
        out.append({"start": a, "end": b, "title": title,
                    "thumb": " ".join(str(s.get("thumb") or "").split()[:5]),
                    "why": str(s.get("why") or "")[:120]})
    return out


def _askers():
    """Best reader first. One request per stream, so Gemini - long
    context, the best judge of a storyline - goes first here (the policy
    read keeps it last to save its 20-a-day for the clip watch pass)."""
    try:
        from autoreel import llm_highlights as llm
    except Exception:
        return []
    out = []
    avail = sorted(llm.all_available(), key=lambda pk: pk[0] != "gemini")
    for provider, key in avail:
        try:
            model = llm.resolve_model(provider, key, "")
        except Exception:
            continue
        ask = llm.asker_for(provider)
        out.append((f"{provider}:{model}",
                    lambda prompt, ask=ask, key=key, model=model: ask(key, model, prompt)))
    return out


def pick(segments, duration: float, n: int, lo: float, hi: float, askers=None,
         blocked: Sequence[Tuple[float, float]] = ()) -> List[dict]:
    if askers is None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return []
        askers = _askers()
    text = condensed(segments, blocked=blocked)
    if not text or not askers:
        return []
    prompt = PROMPT.format(lo=int(lo // 60), hi=int(hi // 60), n=max(1, n)) + text
    # Every model in turn, twice round: free models refuse, run out of
    # room or ignore the length now and then (10-29: Gemini SAFETY, NVIDIA
    # out of tokens, then nothing - and the episode was lost).
    for _round in range(2):
        for label, ask in askers:
            try:
                reply = ask(prompt)
            except Exception:
                reply = ""
            found = parse(reply, duration, lo, hi)
            if found:
                for f in found:
                    f["model"] = label
                return found[:n]
    return []


def thumb_text(words: str, title: str) -> str:
    """The thumbnail's words, held to the title rules - and never about
    kids (10-29 dry run: "SHE CALLED ME A PEDO?")."""
    from utils import text_guard

    w = " ".join(str(words or "").split())
    bad = not w
    try:
        from autoreel.safe_text import child_risk
        bad = bad or bool(child_risk(w))
    except Exception:
        pass
    bad = bad or bool(re.search(r"pedo|predator|groom|minor|underage|kid", w, re.I))
    try:
        bad = bad or bool(text_guard.youtube_title_risk(w))
    except Exception:
        pass
    return text_guard.title(title if bad else w, fallback=title)


def story_title(title: str) -> str:
    """His name first, one line, through every YouTube title rule."""
    from utils import text_guard

    t = " ".join(str(title or "").split()).strip(" -|")
    if not re.match(r"stackswopo\b", t, re.I):
        t = f"Stackswopo {t[:1].lower() + t[1:] if t[:2].istitle() else t}"
    return text_guard.title(t, fallback="Stackswopo Stream Story")


# ── what must not be in it ───────────────────────────────────────────

_SLUR_ONLY = re.compile(r"\bslurs?\b", re.I)
_MORE_THAN_A_SLUR = re.compile(
    r"monkey|ape|animal|dehumaniz|threat|kill|sex|rape|minor|kid|child|attack|"
    r"harass|violen|genocide|lynch|dox", re.I)


def muted_already(flag: dict) -> bool:
    """A policy flag that is only "he said a slur": the censor already
    silences every slur, so the episode keeps the moment (10-29: 29 such
    flags turned a 31-minute story into 9 minutes of pieces). A slur WITH
    something more - an animal comparison, a threat, sexual talk - is still
    cut."""
    why = str(flag.get("why") or "")
    return (flag.get("category") == "hate" and bool(_SLUR_ONLY.search(why))
            and not _MORE_THAN_A_SLUR.search(why))


def risky_spans(cfg, source: str, segments, a: float, b: float,
                strangers=None) -> List[Tuple[float, float]]:
    """Everything inside [a, b] that stays off YouTube."""
    from utils import kid_guard

    inside = [s for s in segments or []
              if float(s.get("start") or 0) < b and float(s.get("end") or 0) > a]
    spans = [(x, y) for x, y, _ in kid_guard.ranges(inside)]
    spans += [(x, y) for x, y, _ in kid_guard.spoken_risks(inside)]
    flags, _note = kid_guard._policy(inside)
    spans += [(max(0.0, f["start"] - PAD), f["end"] + PAD) for f in flags
              if not muted_already(f)]
    try:
        from utils.recap import stranger_spans, _shows_gambling

        if strangers is None:
            strangers = stranger_spans(source)
        spans += [(x, y) for x, y in strangers if x < b and y > a]
        step = 300.0
        t = a
        while t < b:
            if _shows_gambling(source, t, min(b, t + step)):
                spans.append((t, min(b, t + step)))
            t += step
    except Exception as exc:
        print(f"[Stories] screen checks skipped ({exc})")
    merged: List[Tuple[float, float]] = []
    for x, y in sorted((max(a, x), min(b, y)) for x, y in spans if y > a and x < b):
        if merged and x <= merged[-1][1] + 3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], y))
        else:
            merged.append((x, y))
    return merged


def keep_pieces(a: float, b: float, cuts: Sequence[Tuple[float, float]],
                shortest: float = 4.0) -> List[Tuple[float, float]]:
    out, at = [], a
    for x, y in sorted(cuts):
        if x > at + shortest:
            out.append((at, x))
        at = max(at, y)
    if b > at + shortest:
        out.append((at, b))
    return out


# ── the episode ──────────────────────────────────────────────────────

def description(title: str, why: str, stream_title: str, stream_date: str,
                full: str, uncut: str) -> str:
    lines = []
    if full:
        lines.append(f"▶ The whole stream on YouTube: {full}")
    if uncut:
        lines.append(f"▶ Uncut & uncensored on Rumble: {uncut}")
    lines.append("▶ Every stream, every minute: https://www.youtube.com/@wopovod")
    lines.append("")
    when = f" ({stream_date})" if stream_date else ""
    lines.append(f"{title} - from Stackswopo's \"{stream_title}\" stream{when}.")
    if why:
        lines.append(why[0].upper() + why[1:] if why else "")
    lines += ["", "#Stackswopo #GTARP #StackswopoStream"]
    from utils.disclosure import for_description

    return for_description("\n".join(lines))


def _upload(token_path: str, path: str, title: str, desc: str, privacy: str,
            thumb: str, comment: str) -> str:
    from googleapiclient.http import MediaFileUpload

    from utils import text_guard
    from utils.recap import _youtube

    yt = _youtube(token_path)
    body = {"snippet": {"title": title,
                        "description": text_guard.youtube_description(desc, limit_bytes=4900),
                        "tags": ["Stackswopo", "Stackswopo stream", "Stackswopo full stream",
                                 "GTA RP", "Stackswopo reacts", "Stackswopo funny"],
                        "categoryId": "20"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(path, chunksize=8 * 1024 * 1024,
                                                        resumable=True))
    response = None
    while response is None:
        try:
            from utils.upload_beat import beat
            beat("youtube_story")
        except Exception:
            pass
        _, response = req.next_chunk()
    vid = response["id"]
    if thumb and os.path.isfile(thumb):
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumb)).execute()
        except Exception as exc:
            print(f"[Stories] thumbnail not set ({exc})")
    if comment:
        try:
            yt.commentThreads().insert(part="snippet", body={"snippet": {
                "videoId": vid, "topLevelComment": {"snippet": {
                    "textOriginal": text_guard.text(comment, "comment")}}}}).execute()
        except Exception as exc:
            print(f"[Stories] comment not posted ({exc})")
    return f"https://www.youtube.com/watch?v={vid}"


def make(cfg, source: str, stream_title: str, stream_date: str,
         intro: str = "") -> List[str]:
    """Pick, build, check and upload this stream's story episode(s).
    The URLs uploaded ([] when none). Never raises. `intro`: the channel
    intro to put in front (main.find_intro)."""
    try:
        return _make(cfg, source, stream_title, stream_date, intro)
    except Exception as exc:
        print(f"[Stories] Failed: {exc}")
        return []


def _make(cfg, source, stream_title, stream_date, intro="") -> List[str]:
    s = settings(cfg)
    if not s["enabled"] or s["per_stream"] <= 0:
        return []
    if not os.path.exists(s["token_path"]):
        print("[Stories] The stories channel is not signed in - skipped.")
        return []
    key = os.path.basename(source)
    done = _ledger(cfg)
    if key in done:
        return list(done[key].get("urls") or [])
    from utils.ffmpeg_tools import media_duration
    from utils.recap import transcript_of, seekable_copy, build_from

    duration = media_duration(source) or 0.0
    segments = transcript_of(cfg, source)
    if not segments or duration < s["min_s"]:
        print("[Stories] No transcript (or too short a stream) - no story episode.")
        return []
    # A spare or two: a story with too much to cut is dropped, the next
    # one is used instead.
    try:
        from utils.recap import stranger_spans
        strangers = stranger_spans(source)
    except Exception:
        strangers = []
    stories = pick(segments, duration, s["per_stream"] + 2, s["min_s"], s["max_s"],
                   blocked=strangers)
    if not stories:
        # Not written down: the next run asks again (models come and go).
        print("[Stories] The AI found no story worth an episode in this stream.")
        return []
    from utils.stream_links import link_for
    from utils.templating import strip_full_stream_frame, strip_trailing_stamp

    shown = strip_full_stream_frame(strip_trailing_stamp(stream_title) or stream_title)
    full = link_for(stream_title, stream_date, "youtube")
    uncut = link_for(stream_title, stream_date, "rumble")
    work = tempfile.mkdtemp(prefix="story_", dir=cfg.general.censored_folder)
    urls, notes = [], []
    try:
        cut_from = seekable_copy(source, work)
        for n, st in enumerate(stories, 1):
            if len(urls) >= s["per_stream"]:
                break
            a, b = st["start"], st["end"]
            cuts = risky_spans(cfg, source, segments, a, b, strangers=strangers)
            pieces = keep_pieces(a, b, cuts)
            kept = sum(y - x for x, y in pieces)
            if kept < (b - a) * (1 - MAX_CUT_SHARE) or kept < s["min_s"] * 0.8:
                print(f"[Stories] \"{st['title']}\": {((b - a) - kept) / 60:.1f} of "
                      f"{(b - a) / 60:.1f} min would have to be cut - not used.")
                notes.append(f"dropped (too much to cut): {st['title']}")
                continue
            raw = os.path.join(work, f"story{n}.mp4")
            speed = dict(cfg.general.speed or {})
            print(f"[Stories] Building \"{st['title']}\" ({kept / 60:.1f} min, "
                  f"{len(cuts)} part(s) taken out)...")
            if not build_from([(cut_from, x, y) for x, y in pieces], raw,
                              str(speed.get("video_filter", "") or "")):
                notes.append(f"build failed: {st['title']}")
                continue
            from utils.censor import censor_video

            final = censor_video(raw, work, model_name=cfg.general.censor_model,
                                 bleep_method=cfg.general.censor_bleep_method,
                                 custom_words=cfg.general.censor_custom_words,
                                 device=cfg.general.censor_device,
                                 speed={**speed, "stream_copy_video": True, "video_filter": ""},
                                 padding_ms=cfg.general.censor_padding_ms,
                                 mute_whole_segment=cfg.general.censor_mute_whole_segment
                                 ).output_path
            if getattr(cfg.youtube, "music_guard", False):
                from autoreel.music_guard import guard
                final = guard(final, work)
            title = story_title(st["title"])
            up = final
            try:
                from utils.ffmpeg_tools import prepend_intro
                withi = os.path.join(work, f"story{n}_intro.mp4")
                if intro and prepend_intro(intro, final, withi):
                    up = withi
            except Exception as exc:
                print(f"[Stories] intro skipped ({exc})")
            thumb = ""
            try:
                from autoreel import thumbnail
                thumb = thumbnail.make(final, media_duration(final) or kept,
                                       os.path.join(work, f"story{n}.jpg"),
                                       style="stream", text=thumb_text(st.get("thumb"), title))
            except Exception as exc:
                print(f"[Stories] thumbnail skipped ({exc})")
            desc = description(title, st.get("why", ""), shown, stream_date, full, uncut)
            comment = "\n".join(x for x in (
                f"▶ Full stream: {full}" if full else "",
                f"▶ Uncut on Rumble: {uncut}" if uncut else "") if x)
            url = _upload(s["token_path"], up, title, desc, s["privacy"], thumb, comment)
            print(f"[Stories] Uploaded \"{title}\" to STACKSWOPOVODS: {url}")
            urls.append(url)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    done[key] = {"urls": urls, "notes": notes,
                 "stories": [{k: v for k, v in st.items()} for st in stories]}
    _save(cfg, done)
    if urls:
        try:
            from utils.brain import send_alert
            send_alert("New story episode on STACKSWOPOVODS",
                       "\n".join(urls), urgent=False)
        except Exception:
            pass
    return urls

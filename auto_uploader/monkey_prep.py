"""
monkey_prep.py - rough cuts of Monkey App streams for BinScripts, for a
person to finish and publish. NOTHING HERE UPLOADS ANYTHING.

WHY IT STOPS SHORT OF UPLOADING
    BinScripts is the channel about to earn, and the Monkey channels before
    it were removed for harassment, hate speech and bad language. A bot
    cannot see a stranger's age, or tell a roast the other person laughs at
    from one that hurts them. So this does the slow part and a person makes
    the call: find the Monkey part of a stream, the calls worth keeping,
    and the moments that must not go out.

    python monkey_prep.py "D:\\videos stizz\\'!howl' 5-14-26 Stackswopo Stream.ts"
    python monkey_prep.py --scan "D:\\videos stizz"     which files have Monkey parts

WHAT YOU GET (D:\\BinScripts drafts\\<stream>\\)
    rough_cut.mp4   ~16 min of the best moments, in order. Every swear is
                    bleeped and every slur is cut out (his everyday
                    n-word is muted instead - see below).
    review.html     every moment in the cut with what was said, then every
                    moment LEFT OUT and why - age, "stop recording", looks,
                    sexual, names/socials, slurs - so you can check them.

WHAT IT LEAVES OUT
    hate speech       cut - a bleep still shows what was said. The one
                      exception: the everyday n-word from him, not in an
                      insult, is muted like any swear and KEPT (each one is
                      listed on the review page). Hard-r, every other slur,
                      any slur from the other person - cut.
    age               "how old", "I'm 15", school, grade ... 90 s either side
    asked to stop     "stop recording", "delete that", "I'll report" ...
    looks / identity  insults about how somebody looks or what they are
    personal info     names, @s, "my snap", where they live or go to school
  and LISTS, but keeps in the cut (flirting both ways is the show):
    sexual            sexual lines - each one's place in the cut is listed
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

DRAFTS = r"D:\BinScripts drafts"
TARGET_S = 16 * 60
VIDEO_EXT = (".mp4", ".ts", ".mkv", ".mov", ".flv")

_N = r"(?:1[0-7]|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)"
# (reason, seconds dropped either side, pattern). Hate speech and sexual
# words also come from the censor's own lists (see flags_for).
RULES: List[Tuple[str, float, re.Pattern]] = [
    ("age - check how old they are", 90.0, re.compile(
        r"\b(how old|years? old|i'?m (only )?" + _N + r"\b|i just turned " + _N +
        r"|middle school|high school|junior high|elementary|[5-9]th grade|1[0-2]th grade"
        r"|what grade|freshman|sophomore|my (mom|mama|mother|dad|daddy) (is|gonna|said|be)"
        r"|after school|school tomorrow|i'?m a minor|underage)\b")),
    ("asked to stop / upset", 30.0, re.compile(
        r"(stop recording|don'?t record|stop filming|turn (it|that|the camera) off"
        r"|delete (it|that|this)|take (it|that) down|i didn'?t agree|not on (camera|stream)"
        r"|leave me alone|i'?m (gonna |going to )?report|reporting you|why are you (recording|streaming)"
        r"|that'?s not funny|that'?s (racist|disrespectful|messed up)|you'?re being (racist|weird)"
        r"|stop it|please stop|i'?m crying)")),
    ("looks / identity insult", 12.0, re.compile(
        r"\b(ugly|fat (ass|bitch|boy|girl)|fatass|fatty|bald (head|headed)|big forehead|your teeth"
        r"|big nose|you look like (a|an|some)|nasty|crusty|dusty|built like|no edges|unibrow"
        r"|ain'?t nobody (want|like)|you('?re| are) (dumb|stupid|retarded)|go kill)\b")),
    ("sexual comment", 12.0, re.compile(
        r"\b(show me (your|ya|them)|take (it|that|your \w+) off|sit on (my|it)|suck|nudes?"
        r"|titties|boobs|booty|naked|send pics|wanna (smash|fuck)|smash or pass|your body)\b")),
    ("personal info - name / socials / where they live", 15.0, re.compile(
        r"(my name is|my name'?s|what'?s your (name|snap|insta|ig|number|@)"
        r"|my (snap|snapchat|insta|instagram|ig|tiktok|number|@) (is|be)|follow me|add me on"
        r"|i live (in|on|at|by)|what school|where (do )?you live|@\w{3,})")),
]
HATE = "hate speech (slur)"
SEXUAL_WORDS = "sexual comment"
# Flirting both ways is the show; a sexual line aimed at somebody is a
# judgement call. Those stay in the cut and are listed with where they
# are, so you can trim them. Everything else is left out.
LOOKS_FROM_THEM = "insult from them, at him"
MUTED_N = "n-word (muted, kept)"
RACE_TALK = "race / religion talk - check it"
CHECK_ONLY = {SEXUAL_WORDS, LOOKS_FROM_THEM, MUTED_N, RACE_TALK}
# Listed, not cut: talk about a race, nationality or religion is not hate
# speech by itself, but it is exactly what YouTube's hate policy reads in
# context - the first draft kept "the chinese people" and "a family tree of
# racist people" with nothing pointing at them.
RULES.append((RACE_TALK, 5.0, re.compile(
    r"\b(chinese|mexican|asian|indian|arab|african|haitian|puerto rican|dominican"
    r"|white (people|boy|girl|folks|man|woman)|black (people|folks)|jewish|jews?"
    r"|muslim|christian|pentecostal|immigrants?|illegals?|racist|racism)\b")))
OTHER = "other person"          # autoreel.speaker_id.OTHER


def is_cut(flag: dict) -> bool:
    return flag["reason"] not in CHECK_ONLY


def flags_for(segments: Sequence[dict], hate_spans: Sequence[Tuple[float, float]] = (),
              sexual_spans: Sequence[Tuple[float, float]] = (),
              muted_spans: Sequence[Tuple[float, float]] = ()) -> List[dict]:
    """Every moment that must not go out: [{start, end, reason, text, who}].

    `who` comes from autoreel.speaker_id when the segments were labelled
    ("Stackswopo", "other person", "both", "" for not sure). It never
    clears a flag; the one thing it changes is an insult that came FROM
    the other person AT him - that is listed for you, not cut."""
    out = []
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        low = text.lower()
        s, e = float(seg.get("start", 0)), float(seg.get("end", 0))
        who = str(seg.get("speaker") or "")
        for reason, pad, rule in RULES:
            if rule.search(low):
                if reason.startswith("looks") and who == OTHER:
                    reason = LOOKS_FROM_THEM
                out.append({"start": max(0.0, s - pad), "end": e + pad,
                            "reason": reason, "text": text, "who": who})
    for spans, pad, reason in ((hate_spans, 5, HATE), (sexual_spans, 12, SEXUAL_WORDS),
                               (muted_spans, 0, MUTED_N)):
        for (s, e) in spans:
            out.append({"start": max(0.0, s - pad), "end": e + pad, "reason": reason,
                        "text": "", "who": _speaker_at(segments, s, e)})
    return sorted(out, key=lambda f: f["start"])


def _speaker_at(segments: Sequence[dict], s: float, e: float) -> str:
    for seg in segments:
        for w in seg.get("words") or []:
            if float(w.get("start", 0)) < e and float(w.get("end", 0)) > s:
                return str(w.get("speaker") or seg.get("speaker") or "")
    return ""


def _peaks(curve: Sequence[float], floor: float = 0.3, reach: int = 20) -> List[Tuple[int, float]]:
    # The floor follows the stream, not a fixed number: on the 3/17 Monkey
    # stream only ONE second in 80 minutes reached 0.3, so a fixed 0.3 found
    # one moment and the draft came out empty. The loudest ~8% of the
    # stream's own laughter is what counts as a laugh here - but never so
    # low that plain talk or silence passes for one.
    if curve:
        ranked = sorted(float(v) for v in curve)
        floor = max(0.08, min(floor, ranked[int(0.92 * (len(ranked) - 1))]))
    out = []
    for t, v in enumerate(curve):
        if v < floor:
            continue
        around = curve[max(0, t - reach):t + reach + 1]
        if v >= max(around) and not (out and t - out[-1][0] <= reach):
            out.append((t, float(v)))
    return out


def _snap(segments: Sequence[dict], start: float, end: float) -> Tuple[float, float]:
    """Widen to whole sentences so nobody is cut off mid-word."""
    for seg in segments:
        if float(seg["start"]) <= start < float(seg["end"]):
            start = float(seg["start"])
            break
    for seg in segments:
        if float(seg["start"]) < end <= float(seg["end"]):
            end = float(seg["end"])
            break
    return start, end


def _overlaps(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and a["end"] > b["start"]


MIN_PIECE_S = 15.0


def _clean_piece(m: dict, cuts: Sequence[dict], segments: Sequence[dict]) -> Optional[dict]:
    """The longest-useful stretch of moment `m` that touches no cut flag -
    the one holding the laugh, or nearest it - trimmed to whole sentences.
    None when no clean stretch of MIN_PIECE_S is left."""
    s, e = float(m["start"]), float(m["end"])
    peak = float(m.get("peak", (s + e) / 2))
    free, cur = [], s
    for a, b in sorted((max(s, float(f["start"])), min(e, float(f["end"]))) for f in cuts):
        if a > cur:
            free.append((cur, a))
        cur = max(cur, b)
    if cur < e:
        free.append((cur, e))
    free = [(a, b) for a, b in free if b - a >= MIN_PIECE_S]
    if not free:
        return None
    a, b = min(free, key=lambda ab: 0.0 if ab[0] <= peak <= ab[1]
               else min(abs(ab[0] - peak), abs(ab[1] - peak)))
    inside = [seg for seg in segments
              if float(seg["start"]) >= a and float(seg["end"]) <= b]
    if inside and float(inside[-1]["end"]) - float(inside[0]["start"]) >= MIN_PIECE_S:
        a, b = float(inside[0]["start"]), float(inside[-1]["end"])
    return {"start": a, "end": b}


def pick(curve: Sequence[float], segments: Sequence[dict], flags: Sequence[dict],
         target_s: float = TARGET_S, before: float = 35.0, after: float = 12.0):
    """(moments for the cut, moments left out). Moments are built around
    laughter peaks - with no laughter reading, around the busiest talk."""
    peaks = _peaks(curve)
    if segments and len(peaks) * (before + after) < 2 * target_s:
        # Too few laughs to fill a cut: the busiest talk is the next best
        # sign something is happening. Laughs still rank first (they score
        # higher than talk, which is scaled well under 1).
        span = float(segments[-1]["end"])
        talk = [0] * (int(span // 45) + 1)
        for seg in segments:
            talk[int(float(seg["start"]) // 45)] += len(str(seg.get("text", "")).split())
        busy = [(int(i * 45 + 33), min(0.07, n / 1500.0)) for i, n in enumerate(talk) if n >= 40]
        taken = {t for t, _ in peaks}
        peaks = sorted(peaks + [p for p in busy if all(abs(p[0] - t) > 30 for t in taken)])
    found: List[dict] = []
    for t, score in peaks:
        s, e = _snap(segments, max(0.0, t - before), t + after)
        if found and s <= found[-1]["end"] + 2:
            found[-1]["end"] = max(found[-1]["end"], e)
            found[-1]["score"] = max(found[-1]["score"], score)
        else:
            found.append({"start": s, "end": e, "score": score, "peak": float(t)})
    keep, left_out = [], []
    for m in found:
        hits = [f for f in flags if _overlaps(m, f)]
        cuts = [f for f in hits if is_cut(f)]
        if cuts:
            # Keep the clean part around the laugh instead of losing the
            # whole moment to one flagged line near its edge.
            piece = _clean_piece(m, cuts, segments)
            if piece is None:
                left_out.append({**m, "reasons": sorted({f["reason"] for f in cuts}),
                                 "checks": sorted({f["reason"] for f in hits if not is_cut(f)})})
                continue
            m = {**m, **piece}
            hits = [f for f in flags if _overlaps(m, f)]
        m["checks"] = sorted({f["reason"] for f in hits if not is_cut(f)})
        keep.append(m)
    chosen, total = [], 0.0
    for m in sorted(keep, key=lambda m: -m["score"]):
        if total + (m["end"] - m["start"]) <= target_s:
            chosen.append(m)
            total += m["end"] - m["start"]
    return sorted(chosen, key=lambda m: m["start"]), left_out


def monkey_runs(source: str, min_seconds: float = 3 * 60) -> List[Tuple[float, float]]:
    """Where the stream is on Monkey App, by looking at it once a minute."""
    from autoreel.vod_segments import segments_for

    return [(s.start, s.end) for s in segments_for(source, min_seconds=min_seconds)
            if s.kind == "monkey"]


def _general() -> dict:
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            return json.load(f).get("general") or {}
    except (OSError, ValueError):
        return {}


def _cut_copy(source: str, start: float, end: float, out: str) -> bool:
    done = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-to",
                           f"{end:.2f}", "-i", source, "-map", "0:v:0", "-map", "0:a:0",
                           "-c", "copy", out], capture_output=True, text=True)
    return done.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0


def _words(work_dir: str, video: str) -> List[dict]:
    from utils.censor import words_cache_path

    path = words_cache_path(work_dir, os.path.splitext(os.path.basename(video))[0])
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("segments") or []) if isinstance(data, dict) else list(data)
    except (OSError, ValueError):
        return []


def _spans(segments: Sequence[dict], category: str) -> List[Tuple[float, float]]:
    from autoreel.compliance import ComplianceEngine

    engine = ComplianceEngine(only_categories=(category,))
    return [(v.start, v.end) for v in engine.scan_segments(segments)]


# The everyday n-word, the way he says it all stream. Muted by the censor
# like any swear and KEPT (decided 2026-10-10: cutting every moment with
# it left 0 minutes of a real Monkey stream). The hard-r and every other
# slur still cut - see utils/clip_queue.SEVERE_SLURS.
EVERYDAY_N = frozenset({"nigga", "niggas", "niggaz", "nigg", "niggah"})
# An n-word inside an insult is aimed at somebody - that is cut, not muted.
HOSTILE = re.compile(r"\b(ugly|dumb|stupid|bitch|monkey|ape|coon|slave|cotton|retard\w*"
                     r"|fat|broke|poor|go back|kill|dirty|nasty|stinky|smelly)\b")


def hate_split(segments: Sequence[dict]):
    """(spans to CUT, spans that are only MUTED) for hate-speech hits.

    Muted only: the everyday n-word, said by Stacks (or by nobody we can
    tell), in a line that is not an insult. Cut: any other slur, any slur
    from the other person, any slur inside an insult."""
    from autoreel.compliance import ComplianceEngine

    engine = ComplianceEngine(only_categories=("hate_speech",))
    cut, muted = [], []
    for seg in segments:
        low = str(seg.get("text") or "").lower()
        for v in engine.scan_words(seg.get("words") or [], seg.get("start"), seg.get("end")):
            plain = re.sub(r"[^a-z]", "", str(v.word or "").lower())
            who = _speaker_at([seg], v.start, v.end) or str(seg.get("speaker") or "")
            if plain in EVERYDAY_N and who != OTHER and not HOSTILE.search(low):
                muted.append((v.start, v.end))
            else:
                cut.append((v.start, v.end))
    return cut, muted


def censor_all(video: str, work_dir: str):
    """Every swear bleeped (scope "all"). (censored path, transcript)."""
    from utils.censor import censor_video
    from utils.clip_queue import scope_categories

    g = _general()
    result = censor_video(video, work_dir,
                          model_name=g.get("censor_model", "base"),
                          bleep_method=g.get("censor_bleep_method", "beep"),
                          custom_words=tuple(g.get("censor_custom_words", ()) or ()),
                          device=g.get("censor_device") or None,
                          speed={"stream_copy_video": True},
                          padding_ms=int(g.get("censor_padding_ms", 250)),
                          mute_whole_segment=False,
                          only_categories=scope_categories("all"))
    return result.output_path, _words(work_dir, video)


def _clock(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _said(segments: Sequence[dict], start: float, end: float, limit: int = 420) -> str:
    from autoreel.speaker_id import tagged

    text = tagged([s for s in segments
                   if float(s["start"]) < end and float(s["end"]) > start])
    return text if len(text) <= limit else text[:limit] + " ..."


def review_page(title: str, chosen: Sequence[dict], left_out: Sequence[dict],
                flags: Sequence[dict]) -> str:
    """The checklist that goes with the rough cut."""
    def rows(items, cols):
        return "".join("<tr>" + "".join(f"<td>{c(i)}</td>" for c in cols) + "</tr>"
                       for i in items)
    e = html.escape
    kept = rows(chosen, [lambda m: _clock(m["at_cut"]), lambda m: e(m["where"]),
                         lambda m: f"{m['end'] - m['start']:.0f}s",
                         lambda m: e(", ".join(m["checks"])) or "-",
                         lambda m: e(m["said"])])
    out = rows(left_out, [lambda m: e(m["where"]), lambda m: e(", ".join(m["reasons"])),
                          lambda m: e(m["said"])])
    allf = rows(flags, [lambda f: e(f["where"]), lambda f: e(f.get("who") or "not sure"),
                        lambda f: e(f["reason"]), lambda f: e(f.get("text", ""))])
    ages = [f for f in flags if f["reason"].startswith("age")]
    theirs = sum(1 for f in ages if f.get("who") in (OTHER, "both"))
    warn = (f"<p class=warn>{len(ages)} moment(s) mention age or school"
            + (f" - {theirs} of them said by the other person" if theirs else "")
            + ". Watch those calls before you publish anything from that part of the "
              "stream.</p>" if ages else "")
    return f"""<!doctype html><meta charset=utf-8><title>{e(title)} - review</title>
<style>body{{font:15px system-ui;margin:24px;max-width:1100px}}td,th{{border-bottom:1px solid #ddd;
padding:6px;vertical-align:top;text-align:left}}table{{border-collapse:collapse;width:100%}}
.warn{{background:#fee;padding:10px;border-left:4px solid #c00}}h2{{margin-top:32px}}</style>
<h1>{e(title)}</h1>
<p><b>Nothing has been uploaded.</b> rough_cut.mp4 is in this folder: every swear bleeped,
every slur cut out except his everyday n-word, which is muted and listed below as
"n-word (muted, kept)". Watch it, finish the edit, and publish it yourself.</p>{warn}
<p>Check every face in the cut. Nothing here can tell how old someone is.</p>
<h2>In the cut ({len(chosen)} moments)</h2><table><tr><th>At</th><th>From the stream</th>
<th>Length</th><th>Check</th><th>What was said</th></tr>{kept}</table>
<h2>Left out ({len(left_out)} good moments that were flagged)</h2><table><tr><th>From the stream</th>
<th>Why</th><th>What was said</th></tr>{out}</table>
<h2>Every flag in the Monkey part ({len(flags)})</h2><table><tr><th>From the stream</th><th>Who</th>
<th>Why</th><th>Line</th></tr>{allf}</table>
<p>Who said it is worked out from the voices (STACKS / THEM). It is right most of the
time, not always - when two people talk at once it says "not sure".</p>"""


def _folder_name(source: str) -> str:
    stem = os.path.splitext(os.path.basename(source))[0]
    return re.sub(r"[^\w\- ]+", "", stem).strip()[:80] or "stream"


def prepare(source: str, drafts: str = DRAFTS, target_s: float = TARGET_S, say=print) -> str:
    """Rough cut + review page for one stream. The draft folder, or ""."""
    from autoreel.laughter import listen
    from utils.recap import build_from

    runs = monkey_runs(source)
    if not runs:
        say(f"[Monkey] No Monkey App part found in {os.path.basename(source)}.")
        return ""
    out_dir = os.path.join(drafts, _folder_name(source))
    work = os.path.join(out_dir, "_work")
    os.makedirs(work, exist_ok=True)
    say(f"[Monkey] {os.path.basename(source)}: {len(runs)} Monkey part(s), "
        f"{sum(e - s for s, e in runs) / 60:.0f} min.")
    pool, left_all, flags_all = [], [], []
    try:
        for k, (run_s, run_e) in enumerate(runs):
            piece = os.path.join(work, f"monkey_{k}.mp4")
            if not _cut_copy(source, run_s, run_e, piece):
                say(f"[Monkey] Could not copy part {k + 1}; skipping it.")
                continue
            say(f"[Monkey] Part {k + 1}: transcribing and bleeping every swear...")
            censored, segments = censor_all(piece, work)
            try:
                from autoreel.speaker_id import label as label_voices

                voices = label_voices(piece, segments, say=say)
                say(f"[Monkey] Part {k + 1}: Stackswopo is "
                    f"{voices.get('streamer_share', 0):.0%} of the talking.")
            except Exception as exc:
                say(f"[Monkey] Could not tell the voices apart ({exc}) - "
                    "the flags will not say who.")
            cut_hate, muted_n = hate_split(segments)
            flags = flags_for(segments, cut_hate, _spans(segments, "sexual_content"),
                              muted_spans=muted_n)
            say(f"[Monkey] Part {k + 1}: {len(muted_n)} everyday n-word(s) muted and kept, "
                f"{len(cut_hate)} slur(s) cut.")
            say(f"[Monkey] Part {k + 1}: {len(flags)} flagged moment(s); listening for laughs...")
            chosen, left = pick(listen(piece, say=say), segments, flags, target_s)
            for m in chosen + left:
                m["where"] = _clock(run_s + m["start"])
                m["said"] = _said(segments, m["start"], m["end"])
            for f in flags:
                f["where"] = _clock(run_s + f["start"])
            pool += [{**m, "_src": censored, "_k": k} for m in chosen]
            left_all += left
            flags_all += flags
        picked, total = [], 0.0
        for m in sorted(pool, key=lambda m: -m["score"]):
            if total + (m["end"] - m["start"]) <= target_s:
                picked.append(m)
                total += m["end"] - m["start"]
        picked.sort(key=lambda m: (m["_k"], m["start"]))
        if not picked:
            say("[Monkey] Nothing safe and funny enough to cut - see review.html.")
        cut = os.path.join(out_dir, "rough_cut.mp4")
        placed = build_from([(m["_src"], m["start"], m["end"]) for m in picked], cut) if picked else []
        kept = []
        for at, _end, i in placed:
            kept.append({**picked[i], "at_cut": at})
        with open(os.path.join(out_dir, "review.html"), "w", encoding="utf-8") as f:
            f.write(review_page(os.path.basename(source), kept, left_all, flags_all))
        say(f"[Monkey] Done: {len(kept)} moments, {total / 60:.1f} min -> {out_dir}")
        return out_dir
    finally:
        shutil.rmtree(work, ignore_errors=True)


SCAN_EVERY_S = 300.0


def rough_monkey_minutes(source: str, every: float = SCAN_EVERY_S) -> float:
    """About how much of a video is Monkey App, from one look every 5
    minutes - twelve looks an hour instead of sixty. Enough to choose
    which streams to prepare; prepare() looks properly at the ones chosen."""
    from autoreel.vod_segments import _seconds_long, read_kinds

    marks = read_kinds(source, _seconds_long(source), every)
    return round(sum(1 for _, kind in marks if kind == "monkey") * every / 60, 1)


def _likely_first(names: Sequence[str]) -> List[str]:
    """Titles that say Monkey/howl first - they are the likeliest."""
    hint = re.compile(r"monkey|howl|omegle|ome\.?tv", re.I)
    return sorted(names, key=lambda n: (not hint.search(n), n.lower()))


def scan(folder: str, drafts: str = DRAFTS, say=print) -> List[dict]:
    """Which videos in `folder` have a Monkey part, and how long. Saved to
    <drafts>\\scan.json so a second look is free."""
    path = os.path.join(drafts, "scan.json")
    try:
        with open(path, encoding="utf-8") as f:
            seen = {r["file"]: r for r in json.load(f)}
    except (OSError, ValueError):
        seen = {}
    found = []
    for name in _likely_first(os.listdir(folder)):
        full = os.path.join(folder, name)
        if not name.lower().endswith(VIDEO_EXT) or ".temp." in name.lower():
            continue
        size = os.path.getsize(full)
        row = seen.get(full)
        if not row or row.get("bytes") != size:
            try:
                minutes = rough_monkey_minutes(full)
            except Exception as exc:
                say(f"[Monkey] Could not read {name} ({exc}).")
                continue
            row = {"file": full, "bytes": size, "monkey_min": minutes}
            seen[full] = row
            os.makedirs(drafts, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(list(seen.values()), f, indent=1)
        say(f"  {row['monkey_min']:6.1f} min Monkey   {name}")
        found.append(row)
    return found


def main(argv: Optional[Sequence[str]] = None) -> int:
    # File names like '5⧸1⧸26' (yt-dlp's stand-in for a slash) cannot be
    # printed to a Windows console or log in its old code page.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Rough cuts of Monkey App streams for "
                                 "BinScripts. Uploads nothing.")
    ap.add_argument("videos", nargs="*", help="stream files to prepare")
    ap.add_argument("--scan", metavar="FOLDER", help="list which videos have a Monkey part")
    ap.add_argument("--out", default=DRAFTS, help=f"where drafts go (default {DRAFTS})")
    ap.add_argument("--minutes", type=float, default=TARGET_S / 60, help="rough cut length")
    args = ap.parse_args(argv)
    if args.scan:
        scan(args.scan, args.out)
    for video in args.videos:
        prepare(video, args.out, args.minutes * 60)
    if not args.scan and not args.videos:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

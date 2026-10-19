"""Cut the parts of a full stream that talk about kids in a sexual way.

10-17: a stream about a predator case went up on wopovod and 25 parts
(21 min) had to be cut by hand in Studio. That talk is a channel strike,
so before every full-stream YouTube upload the censor pass's transcript
is read, those parts are cut out of the YouTube copy (Rumble keeps the
whole stream), and the operator gets the times on Discord. When the topic
runs too long to cut, or the cut fails, the stream goes up PRIVATE.
"""
import os
import re
import subprocess

SEX = re.compile(
    r"\b(sex\w*|penis\w*|vagina\w*|pussy|dick\w*|genital\w*|nut(?:ted|ting)?|"
    r"cum\w*|rap(?:e|ed|ing|ist)|molest\w*|groom\w*|"
    r"fuck(?:ed|ing)? (?:her|him|them|a kid|kids|a minor)|intercourse|naked|"
    r"nudes?|horny|sucks? (?:dick|cock))\b", re.I)
KID = re.compile(r"\b(pedo\w*|paedo\w*|minors?|underage|kids?|child\w*|teens?|"
                 r"jailbait|loli\w*)\b", re.I)
STRONG = re.compile(r"pedo|paedo|underage|\bminors?\b|jailbait", re.I)
PAD = 8.0          # seconds kept off each side of a flagged line
JOIN = 20.0        # parts closer than this become one cut
MAX_CUT_S = 45 * 60
MAX_SHARE = 0.25   # more than this much of the stream: private, not cut


def _child_risk(text):
    try:
        from autoreel.safe_text import child_risk
        return bool(child_risk(text))
    except Exception:
        return False


def ranges(segments, pad=PAD, join=JOIN):
    """[(start, end, [lines])] to cut, in source seconds."""
    hits = []
    for i, s in enumerate(segments or []):
        text = str(s.get("text") or "")
        if not (KID.search(text) or _child_risk(text)):
            continue
        window = " ".join(str(x.get("text") or "") for x in segments[max(0, i - 1): i + 2])
        if SEX.search(window) or STRONG.search(text):
            hits.append((max(0.0, float(s["start"]) - pad), float(s["end"]) + pad, text.strip()))
    merged = []
    for a, b, t in sorted(hits):
        if merged and a <= merged[-1][1] + join:
            merged[-1][1] = max(merged[-1][1], b)
            merged[-1][2].append(t)
        else:
            merged.append([a, b, [t]])
    return [(a, b, ts) for a, b, ts in merged]


def keep_spans(duration, cuts):
    """The parts that stay, as [(start, end)]."""
    keep, t = [], 0.0
    for a, b, *_ in sorted(cuts):
        if a - t > 0.5:
            keep.append((t, a))
        t = max(t, b)
    if duration - t > 0.5:
        keep.append((t, duration))
    return keep


def hms(x):
    x = max(0, int(x))
    return f"{x // 3600}:{x % 3600 // 60:02d}:{x % 60:02d}"


def _duration(path):
    from utils.ffmpeg_tools import media_duration
    return media_duration(path)


def _drop(path):
    try:
        os.remove(path)
    except OSError:
        pass


def cut(src, cuts, out, duration):
    """Stream-copy src without the cuts into out. True when out is right."""
    keep = keep_spans(duration, cuts)
    if not keep:
        return False
    lst = out + ".concat.txt"
    p = os.path.abspath(src).replace("\\", "/").replace("'", "'\\''")
    with open(lst, "w", encoding="utf-8") as f:
        for a, b in keep:
            f.write(f"file '{p}'\ninpoint {a:.3f}\noutpoint {b:.3f}\n")
    r = None
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
             "-safe", "0", "-i", lst, "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
             "-avoid_negative_ts", "make_zero", out],
            capture_output=True, text=True, timeout=4 * 3600)
    except Exception as exc:
        print(f"[Kids] cut error: {exc}")
    finally:
        _drop(lst)
    if not r or r.returncode != 0:
        print(f"[Kids] ffmpeg failed: {((r.stderr if r else '') or '')[-300:]}")
        _drop(out)
        return False
    want = sum(b - a for a, b in keep)
    got = _duration(out) or 0
    if abs(got - want) > 5 + 2 * len(keep):
        print(f"[Kids] cut came out {got:.0f}s, wanted {want:.0f}s - not used")
        _drop(out)
        return False
    return True


def _alert(title, message, urgent=True):
    try:
        from utils.brain import send_alert
        send_alert(title, message, urgent=urgent)
    except Exception as exc:
        print(f"[Kids] alert failed: {exc}")


def _segments(cfg, *paths):
    from utils.clip_runner import _load_segments
    for path in paths:
        segs = _load_segments(cfg, path) if path else None
        if segs:
            return segs
    return None


def make_safe(cfg, video_path, yt_source, out_dir, alert=None):
    """(path to upload instead or None, reason to go private or "")."""
    say = alert or _alert
    segs = _segments(cfg, yt_source, video_path)
    if not segs:
        print("[Kids] no transcript cached - child-safety check skipped")
        return None, ""
    duration = _duration(yt_source) or float(segs[-1].get("end") or 0)
    name = os.path.basename(video_path or yt_source)
    # Kid talk by pattern, plus the whole-policy AI read (utils/policy_guard).
    found = [(a, min(b, duration), "kid talk") for a, b, _ in ranges(segs) if a < duration]
    flags, note = _policy(segs)
    # The AI read only CUTS child-safety calls from a strong (cloud) model.
    # 10-19 test: the small local model called a phone-call GAME's threats
    # "harassment" a dozen times in 15 minutes - cutting on that would gut
    # the stream. Everything else is reported, for a person to judge.
    strong = [f for f in flags if f["severity"] == "cut" and f["category"] == "child_safety"
              and not str(f.get("model", "")).startswith("local")]
    found += [(f["start"], min(f["end"], duration), f["category"] + ": " + f["why"])
              for f in strong if f["start"] < duration]
    found = merged(found)
    warns = "\n".join([f"{hms(f['start'])} {f['category']}: {f['why']}"
                       for f in flags if f not in strong][:15])
    extra = (f"\n\nWorth a look (not cut):\n{warns}" if warns else "") + (f"\n({note})" if note else "")
    if not found:
        if warns:
            say("Check these parts of the new stream",
                f"{name}: nothing clear enough to cut, but the policy read flagged:\n{warns}",
                urgent=False)
        return None, ""
    total = sum(b - a for a, b, _ in found)
    times = "\n".join(f"{hms(a)} - {hms(b)}  {why}" for a, b, why in found)
    print(f"[Kids] {len(found)} risky part(s), {total / 60:.1f} min")
    if total > MAX_CUT_S or total > MAX_SHARE * max(duration, 1):
        say("Too much to cut - YouTube upload PRIVATE",
            f"{name}: {len(found)} parts, {total / 60:.0f} min break YouTube's rules - too much "
            f"to cut. It goes up PRIVATE; check it in Studio. Times in the stream:\n{times}{extra}")
        return None, "too much to cut"
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(yt_source))[0] + "_KIDSAFE.mp4")
    if cut(yt_source, found, out, duration):
        say(f"Cut {len(found)} risky part(s) out of the YouTube upload",
            f"{name}: cut {len(found)} parts ({total / 60:.1f} min) before wopovod. "
            f"Rumble keeps them. Times in the stream:\n{times}{extra}", urgent=False)
        return out, ""
    say("Risky-part cut FAILED - YouTube upload PRIVATE",
        f"{name}: {len(found)} parts ({total / 60:.1f} min) could not be cut. "
        f"It goes up PRIVATE; cut these in Studio:\n{times}{extra}")
    return None, "risky-part cut failed"


def merged(parts, join=JOIN):
    """Overlapping/near cuts become one, keeping every reason."""
    out = []
    for a, b, why in sorted(parts):
        if out and a <= out[-1][1] + join:
            out[-1] = (out[-1][0], max(out[-1][1], b),
                       out[-1][2] if why in out[-1][2] else out[-1][2] + ", " + why)
        else:
            out.append((a, b, why))
    return out


def _policy(segs):
    """(flags, note) from utils/policy_guard; never raises."""
    try:
        from utils import policy_guard
        return policy_guard.review(segs)
    except Exception as exc:
        print(f"[Policy] review error: {exc}")
        return [], f"policy review error: {exc}"

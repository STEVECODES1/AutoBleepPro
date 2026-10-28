"""Read a whole stream's transcript against YouTube's rules before upload.

Owner, 10-19: not just the kid check - the whole policy. The transcript is
cut into 5-minute pieces and an AI (whichever autoreel/llm_highlights has
configured, free ones first) reads each one against the Community
Guidelines. A clear break comes back as "cut" (kid_guard cuts it out of the
YouTube copy, like the kid talk); "warn" goes to Discord with the times for
a person to look at. Swearing, trash talk, game/RP violence and pranks are
allowed and must not be flagged - the prompt says so.

Never fatal: no model, a refusal or a timeout just means no extra cuts.
"""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

WINDOW_S = 300
PAD_S = 5.0
WORKERS = 4
BUDGET_S = 20 * 60  # the whole review; what is left over is skipped and said
CATEGORIES = ("child_safety", "hate", "harassment", "violent_threat", "dangerous", "sexual")

RULES = """NEW TASK - ignore any earlier instructions about clips or highlights.
You review a transcript piece from a GTA RP / reaction livestream before it is uploaded to YouTube.
ALLOWED, never flag: swearing, insults between friends, trash talk, video-game or role-play
violence, scam-bait or prank role-play, dark jokes, crude jokes. Slurs are already muted.
Most of these streams are GAMES: phone-call and scam simulators, GTA role-play. Threats, hostages,
insults and demands said to game characters or in role-play are part of the game - NOT real.
harassment and violent_threat are only for REAL people outside the game.
Flag ONLY what YouTube's Community Guidelines remove or strike:
- child_safety: any sexual talk involving minors, sexualizing a child, grooming
- hate: dehumanizing attacks on people for race, religion, gender, sexuality, disability or nationality
- harassment: threats or sexual harassment aimed at a real private person, telling viewers to harass
  someone, doxxing (a real private person's address, phone, workplace or full name with location)
- violent_threat: real threats or encouraging real violence against real people
- dangerous: encouraging self-harm or suicide, instructions for weapons, drugs or dangerous stunts
- sexual: graphic, explicit description of sex acts
Each line starts with its time in seconds, like [1234].
Answer with JSON only:
{"flags": [{"category": "<one of the above>", "severity": "cut" or "warn",
            "start": <seconds>, "end": <seconds>, "why": "<under 12 words>"}]}
"cut" only for a clear violation, "warn" when unsure. Nothing wrong: {"flags": []}"""


def windows(segments, size=WINDOW_S):
    """[(start, end, text with [seconds] per line)] covering the transcript."""
    out, cur, start = [], [], None
    for s in segments or []:
        t, text = float(s.get("start") or 0), str(s.get("text") or "").strip()
        if not text:
            continue
        if start is None:
            start = t
        if t - start >= size and cur:
            out.append((start, t, "\n".join(cur)))
            cur, start = [], t
        cur.append(f"[{int(t)}] {text}")
    if cur:
        end = float((segments or [{}])[-1].get("end") or start)
        out.append((start, end, "\n".join(cur)))
    return out


def parse(reply, lo, hi):
    """Flags from a model reply, clamped to the piece [lo, hi]."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return []
    try:
        flags = json.loads(m.group(0)).get("flags") or []
    except (ValueError, AttributeError):
        return []
    out = []
    for f in flags:
        try:
            cat = str(f.get("category", "")).strip().lower()
            sev = "cut" if str(f.get("severity", "")).lower() == "cut" else "warn"
            a, b = float(f.get("start", lo)), float(f.get("end", f.get("start", lo)))
        except (TypeError, ValueError, AttributeError):
            continue
        if cat not in CATEGORIES:
            continue
        a, b = max(lo, min(a, b)) - PAD_S, min(hi, max(a, b)) + PAD_S
        out.append({"category": cat, "severity": sev, "start": max(0.0, a), "end": b,
                    "why": str(f.get("why", ""))[:80]})
    return out


def _askers():
    """[(label, ask(prompt) -> str)] for every configured model, best first."""
    try:
        from autoreel import llm_highlights as llm
    except Exception:
        return []
    out = []
    # Gemini's free tier is 20 requests a day (10-28), and the clip watch
    # pass - the only judge that WATCHES and LISTENS for the funny part -
    # needs them. A 3-hour stream is ~36 policy pieces, which used to spend
    # the whole day's Gemini quota before a single clip was judged. So the
    # policy read uses every other model first and Gemini only as the last
    # resort when nothing else is configured.
    available = list(llm.all_available())
    others = [(p, k) for p, k in available if p != "gemini"]
    available = others if others else available
    for provider, key in available:
        try:
            model = llm.resolve_model(provider, key, "")
        except Exception:
            continue
        ask = llm.asker_for(provider)
        out.append((f"{provider}:{model}",
                    lambda prompt, ask=ask, key=key, model=model: ask(key, model, prompt)))
    return out


def review(segments, askers=None, budget_s=BUDGET_S, clock=time.monotonic):
    """(flags, note). note says what was skipped, if anything."""
    if askers is None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return [], "skipped under tests"
        askers = _askers()
    if not askers:
        return [], "no AI model configured - policy review skipped"
    pieces = windows(segments)
    deadline = clock() + budget_s
    skipped = [0]

    def one(i):
        lo, hi, text = pieces[i]
        for k in range(len(askers)):  # spread the load, fall through on silence
            if clock() > deadline:
                skipped[0] += 1
                return []
            label, ask = askers[(i + k) % len(askers)]
            try:
                reply = ask(f"{RULES}\n\nTranscript piece:\n{text}")
            except Exception:
                reply = ""
            if reply:
                found = parse(reply, lo, hi)
                for f in found:
                    f["model"] = label
                return found
        return []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        flags = [f for found in pool.map(one, range(len(pieces))) for f in found]
    note = f"{skipped[0]} of {len(pieces)} pieces not reviewed (time limit)" if skipped[0] else ""
    return sorted(flags, key=lambda f: f["start"]), note

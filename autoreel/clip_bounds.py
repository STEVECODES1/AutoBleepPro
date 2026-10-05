"""Start each clip on the line that opens the bit, end it after the reaction.

WHY
---
The picker chooses a WINDOW around a funny moment, and a window's edges
land wherever the scoring put them. On WASSSSUP Clip 03 that meant the
first five seconds were the tail of the previous joke ("we don't want no
women coming in my church looking like whores") before the actual bit
started ("would y'all like to purchase some drugs?"), and the clip ended
one breath after the punchline ("I'm trying to save lives"). A stranger
scrolling sees an unrelated opening, swipes, and the platform shows the
clip to fewer people.

WHAT
----
For every chosen clip, a language model reads the clip's lines (with
times) plus the lines just after it and answers two numbers: the line
the bit starts on, and the line the bit (including the reaction to the
punchline) ends on. The window is moved to those lines.

SAFETY
------
Never fatal, never makes a clip worse on purpose:
  * no model / no answer / unreadable answer -> clip left exactly as is
  * the start can only move LATER (cutting leftovers), never earlier,
    and never past the middle of the clip
  * the result must still be between min_seconds and max_seconds,
    otherwise that edge is put back
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import List, Optional

LEAD_S = 0.25          # a breath before the first word
TAIL_S = 1.5           # the beat after the last line - the reaction
EXTEND_LOOKAHEAD_S = 8.0
PAUSE_SPLIT_S = 0.6

SYSTEM = """\
You edit short-form comedy clips cut from a long live stream (mostly GTA
roleplay). You are given the clip's spoken lines, numbered, with times.
Lines marked [AFTER] are what was said right after the current cut ends.

Answer two things:
- start: the number of the line where THE BIT this clip is about begins.
  If the first lines belong to an earlier, different conversation or
  joke (a leftover the cut caught by accident), skip them. If the clip
  already opens on the bit, answer 1. Never skip the setup the punchline
  needs.
- end: the number of the last line that belongs to the bit, INCLUDING the
  immediate reaction to the punchline (the comeback, the laugh line). It
  may be an [AFTER] line if the reaction continues there. Stop before a
  new topic starts. If the current ending is right, answer the last
  non-[AFTER] line.

Reply with JSON only: {"start": <int>, "end": <int>, "why": "<one short sentence>"}"""


def _words_between(segments: list, t0: float, t1: float) -> list:
    out = []
    for seg in segments:
        if seg.get("end", 0) < t0 or seg.get("start", 0) > t1:
            continue
        words = seg.get("words") or []
        if not words:
            out.append({"start": float(seg.get("start", 0)), "end": float(seg.get("end", 0)),
                        "word": str(seg.get("text", ""))})
            continue
        for w in words:
            s, e = float(w.get("start", 0)), float(w.get("end", 0))
            if e >= t0 and s <= t1:
                out.append({"start": s, "end": e, "word": str(w.get("word", ""))})
    return out


def _lines(words: list) -> list:
    """Words grouped into lines at sentence ends and pauses."""
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = re.search(r"[.?!]\s*$", w["word"].strip()) is not None
        gap = (nxt["start"] - w["end"]) if nxt is not None else 0.0
        pause = nxt is not None and gap >= PAUSE_SPLIT_S
        # Fast, unpunctuated talk transcribes as one 25-second "line",
        # which gives the model nothing to point at. Break long runs at
        # the next small breath, or hard at 10 seconds.
        span = w["end"] - cur[0]["start"]
        long_run = (span >= 5.0 and gap >= 0.2) or span >= 10.0
        if nxt is None or ends or pause or long_run:
            text = " ".join(x["word"].strip() for x in cur if x["word"].strip())
            if text:
                lines.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": text})
            cur = []
    return lines


def _ask(prompt: str, provider: str = "", model: str = "") -> str:
    """One answer from the first configured model that gives one, or ''."""
    from . import llm_highlights as L

    for name, key in L.all_available(provider):
        try:
            if name == L.ANTHROPIC:
                text, why = L._claude(key, L.resolve_model(name, key, model if name == provider else ""),
                                      SYSTEM, prompt, max_tokens=2000, effort="low")
                if text:
                    return text
            elif name == L.GEMINI:
                m = L.resolve_model(name, key, model if name == provider else "")
                payload = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
                           "contents": [{"parts": [{"text": prompt}]}],
                           "safetySettings": L._SAFETY,
                           "generationConfig": {"responseMimeType": "application/json",
                                                "temperature": 0.2}}
                data, problem = L._post_detailed(L._gemini_url(m, key), payload, {})
                parts = (((data or {}).get("candidates") or [{}])[0]
                         .get("content", {}).get("parts") or [])
                text = "".join(p.get("text", "") for p in parts).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def _parse(raw: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return {"start": int(d["start"]), "end": int(d["end"]), "why": str(d.get("why", ""))}
    except (ValueError, KeyError, TypeError):
        return None


def refine_one(spec, segments: list, min_seconds: float, max_seconds: float,
               provider: str = "", model: str = "", ask=None):
    """The same clip with better edges, or the clip unchanged."""
    inside = _lines(_words_between(segments, spec.start, spec.end))
    after = [ln for ln in _lines(_words_between(segments, spec.end, spec.end + EXTEND_LOOKAHEAD_S))
             if ln["start"] >= spec.end - 0.05]
    if len(inside) < 2:
        return spec, ""
    numbered = inside + after
    listing = "\n".join(
        f"{i}. [{ln['start'] - spec.start:5.1f}s]{' [AFTER]' if i > len(inside) else ''} {ln['text']}"
        for i, ln in enumerate(numbered, start=1))
    from .show_bible import bible_block
    prompt = f"{bible_block()}\nClip title: {spec.title}\n\n{listing}"
    reply = ask(prompt) if ask else _ask(prompt, provider, model)
    got = _parse(reply)
    if not got:
        return spec, ""
    s_i = min(max(1, got["start"]), len(inside))
    e_i = min(max(s_i, got["end"]), len(numbered))

    start, end = spec.start, spec.end
    new_start = max(spec.start, numbered[s_i - 1]["start"] - LEAD_S)
    # Only cut leftovers from the front - never past half the clip.
    if new_start <= spec.start + spec.duration / 2:
        start = new_start
    new_end = numbered[e_i - 1]["end"] + TAIL_S
    if e_i < len(numbered):
        new_end = min(new_end, numbered[e_i]["start"] - 0.05)
    end = max(new_end, numbered[e_i - 1]["end"] + 0.3)

    if end - start < min_seconds:
        start = spec.start                      # put the front back first
        if end - start < min_seconds:
            end = max(end, spec.end)
    if end - start > max_seconds:
        end = start + max_seconds
    if abs(start - spec.start) < 0.3 and abs(end - spec.end) < 0.3:
        return spec, got["why"]

    words = _words_between(segments, start, end)
    text = " ".join(w["word"].strip() for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.05)
    return replace(spec, start=round(start, 2), end=round(end, 2),
                   transcript=text or spec.transcript), got["why"]


def refine(specs: List, segments: list, min_seconds: float, max_seconds: float,
           provider: str = "", model: str = "") -> List:
    out = []
    for spec in specs:
        try:
            new, why = refine_one(spec, segments, min_seconds, max_seconds, provider, model)
        except Exception as exc:                   # never cost a clip
            print(f"[Clips] Clip {spec.index:02d}: could not check its edges ({exc}) - kept as cut.")
            new, why = spec, ""
        if new is not spec:
            print(f"[Clips] Clip {spec.index:02d}: start {new.start - spec.start:+.1f}s, "
                  f"end {new.end - spec.end:+.1f}s ({new.duration:.0f}s) - {why}")
        out.append(new)
    return out

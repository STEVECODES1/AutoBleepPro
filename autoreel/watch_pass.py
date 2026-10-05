"""Watch AND listen to every candidate clip before it is cut.

The picker before this read a transcript and looked at two still frames
per candidate. A person deciding whether something is funny uses what
neither of those carries: timing, tone, the room laughing, somebody
getting cut off mid-sentence. Gemini takes real video with sound, so the
shortlist is sent to it as small clips (360p, 15fps, mono) and it
answers the questions a human editor asks:

  * did anybody laugh or react?
  * does the punchline land, and is the reaction inside the clip?
  * would a stranger scrolling past get it in the first three seconds?
  * would you send it to a friend?

Its score re-ranks the candidates and its rejects are dropped. Any
failure - no key, quota spent, a refusal, an unreadable answer - leaves
the earlier picks exactly as they were. Never fatal.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
from typing import List, Optional

BATCH = 5                 # clips per request - keeps each request ~10 MB
MAX_CLIP_BYTES = 4 * 1024 * 1024
KEEP_SCORE = 55           # below this the editor would not post it

PROMPT = """\
You are the editor of Stackswopo's clips channel. Below are {n} short
clips cut from one of his streams. WATCH AND LISTEN to each one - the
sound matters as much as the picture: laughter, tone, timing, people
talking over each other, a voice going quiet after a burn.

For each clip, judge it the way a human editor would before posting:
- laugh: does anyone (him, other players, people in the room) laugh or
  clearly react?
- lands: is there a real punchline, comeback or payoff, AND is the
  reaction to it inside the clip rather than cut off?
- stranger: would someone who has never seen him get it within the first
  three seconds, without context?
- share: would a fan send this to a friend?
Then give a score 0-100 for how well it would do as a Reel/Short, and
keep=true only if you would actually post it.

Also write a title: what happens, in plain words, under 70 characters,
no hashtags, no emoji. NEVER write a slur, a masked word or profanity in
the title - describe the moment instead.

Reply with JSON only:
{{"clips": [{{"n": <clip number>, "laugh": true/false, "lands": true/false,
"stranger": true/false, "share": true/false, "score": <0-100>,
"keep": true/false, "title": "...", "why": "<one short sentence>"}}]}}
"""


def _proxy(source: str, start: float, end: float, out: str) -> Optional[bytes]:
    """A small, watchable copy of one candidate, with its sound."""
    from .gpu import best_h264_encoder

    enc = best_h264_encoder()
    length = max(1.0, end - start)
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", source,
            "-vf", "scale=-2:360,fps=15",
            "-c:v", enc, "-b:v", "400k", "-maxrate", "400k", "-bufsize", "800k",
            "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-movflags", "+faststart", out]
    try:
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=300)
        with open(out, "rb") as fh:
            data = fh.read()
        return data if 0 < len(data) <= MAX_CLIP_BYTES else None
    except (OSError, subprocess.SubprocessError):
        return None


def _ask_gemini(parts: list) -> str:
    """Gemini's reply text for one batch, or ''. Falls to flash-lite on quota."""
    from . import llm_highlights as L

    key = L.api_key(L.GEMINI)
    if not key:
        return ""
    model = L.resolve_model(L.GEMINI, key, "")
    payload = {"contents": [{"parts": parts}],
               "safetySettings": L._SAFETY,
               "generationConfig": {"responseMimeType": "application/json",
                                    "temperature": 0.3}}
    import time

    data, problem = L._post_detailed(L._gemini_url(model, key), payload, {})
    if problem and L._is_transient(problem) and not L.is_quota_exhausted(problem):
        # "Model is experiencing high demand" - usually gone in seconds.
        time.sleep(20)
        data, problem = L._post_detailed(L._gemini_url(model, key), payload, {})
    if problem and "lite" not in model.lower():
        # Out of quota OR still overloaded: the flash-lite sibling is a
        # different pool, still multimodal, still hears the audio.
        lite = L.resolve_lite_model(key, avoid=model)
        if lite:
            print(f"[Watch] {model} unavailable ({str(problem)[:60]}) - using {lite}.")
            data, problem = L._post_detailed(L._gemini_url(lite, key), payload, {})
    if problem:
        print(f"[Watch] Gemini could not watch this batch ({str(problem)[:160]})")
        return ""
    cand = ((data or {}).get("candidates") or [{}])[0]
    return "".join(p.get("text", "") for p in
                   (cand.get("content", {}).get("parts") or [])).strip()


def _parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        clips = json.loads(m.group(0)).get("clips") or []
    except ValueError:
        return {}
    out = {}
    for c in clips:
        try:
            out[int(c["n"])] = {
                "score": float(c.get("score", 0)),
                "keep": bool(c.get("keep", False)),
                "laugh": bool(c.get("laugh", False)),
                "lands": bool(c.get("lands", False)),
                "stranger": bool(c.get("stranger", False)),
                "title": str(c.get("title", "")).strip(),
                "why": str(c.get("why", "")).strip(),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def watch(candidates: list, source_path: str, ask=None) -> List[Optional[dict]]:
    """One verdict per candidate (None where the model gave none)."""
    from .show_bible import bible_block

    verdicts: List[Optional[dict]] = [None] * len(candidates)
    work = tempfile.mkdtemp(prefix="watch_")
    try:
        for b0 in range(0, len(candidates), BATCH):
            batch = candidates[b0:b0 + BATCH]
            parts = [{"text": bible_block() + "\n" + PROMPT.format(n=len(batch))}]
            numbered = []
            for i, h in enumerate(batch, start=1):
                data = _proxy(source_path, h.start, h.end,
                              os.path.join(work, f"c{b0 + i}.mp4"))
                if data is None:
                    continue
                parts.append({"text": f"\nClip {i} ({h.end - h.start:.0f}s):"})
                parts.append({"inline_data": {"mime_type": "video/mp4",
                                              "data": base64.b64encode(data).decode()}})
                numbered.append(i)
            if not numbered:
                continue
            raw = ask(parts) if ask else _ask_gemini(parts)
            got = _parse(raw)
            for i in numbered:
                if i in got:
                    verdicts[b0 + i - 1] = got[i]
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    return verdicts


def _overlaps(a, b) -> bool:
    return a.start < b.end and b.start < a.end


def _with(h, **changes):
    from dataclasses import is_dataclass, replace
    if is_dataclass(h):
        try:
            return replace(h, **changes)
        except TypeError:
            pass
    for k, v in changes.items():
        try:
            setattr(h, k, v)
        except AttributeError:
            pass
    return h


def rerank(picked: list, pool: list, count: int, source_path: str,
           floor: int = 1, ask=None) -> list:
    """The final picks after an editor has watched them, or `picked` as is.

    `picked` is what the text/frames pass chose; `pool` is the wider
    shortlist. A few runners-up from the pool are watched too, so a clip
    that only works with its sound can still make the cut.
    """
    if not picked or not source_path or not os.path.isfile(source_path):
        return picked
    candidates = list(picked)
    for h in sorted(pool, key=lambda x: x.score, reverse=True):
        if len(candidates) >= min(2 * count, 20):
            break
        if not any(_overlaps(h, c) for c in candidates):
            candidates.append(h)

    print(f"[Watch] An editor model is watching and listening to "
          f"{len(candidates)} candidate clip(s)...")
    try:
        verdicts = watch(candidates, source_path, ask=ask)
    except Exception as exc:
        print(f"[Watch] Skipped ({type(exc).__name__}: {exc}) - keeping the picks as they were.")
        return picked
    if not any(verdicts):
        print("[Watch] No verdicts came back - keeping the picks as they were.")
        return picked

    scored = []
    for h, v in zip(candidates, verdicts):
        if v is None:
            continue
        flags = ", ".join(k for k in ("laugh", "lands", "stranger") if v[k]) or "no reaction"
        verdict = "keep" if v["keep"] and v["score"] >= KEEP_SCORE else "drop"
        print(f"[Watch]  {int(h.start // 60):02d}:{int(h.start % 60):02d}  "
              f"{v['score']:3.0f}/100 {verdict:4}  ({flags}) {v['why'][:90]}")
        if verdict == "keep":
            # A runner-up from the scorer's pool has no written title -
            # its "hook" is a raw transcript line, slurs and all. The
            # editor's title replaces it; the text model's title stays.
            from_model = any(h is p for p in picked)
            hook = (getattr(h, "hook", "") if from_model else "") or v["title"] \
                or getattr(h, "hook", "")
            scored.append(_with(h, score=v["score"], hook=hook))
    final = sorted(scored, key=lambda x: x.score, reverse=True)[:count]
    for h in picked:                        # never come back near-empty
        if len(final) >= max(1, floor):
            break
        if not any(_overlaps(h, f) for f in final):
            final.append(h)
    dropped = [h for h in picked if not any(_overlaps(h, f) for f in final)]
    added = [f for f in final if not any(_overlaps(f, p) for p in picked)]
    print(f"[Watch] Kept {len(final)} clip(s): dropped {len(dropped)} the editor "
          f"would not post, added {len(added)} it liked better.")
    return sorted(final, key=lambda x: x.start)

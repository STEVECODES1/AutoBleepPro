"""The recap's edit - what makes it look cut by a person, not glued.

  - an opening card: the stream's title over a blurred still of its best
    moment ("THE RETURN / BEST MOMENTS - 10-09-26")
  - a topic title on each moment ("Stacks negotiates with the medics"),
    written by the AI on this PC from what is said in it
  - burned captions, word by word, with every flagged word starred - the
    audio is bleeped later, the text never shows what was said
  - each moment slides in over the last frame of the one before

All ffmpeg on the GPU, so it runs by itself after every stream. CapCut
does the same things by hand; it has no way to be driven by a program,
which is the whole point here.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, List, Optional, Sequence

W, H = 1920, 1080
SLIDE_S = 0.35
TITLE_IN_S, TITLE_OUT_S = 0.3, 4.3
CARD_S = 3.0
FONT = "Arial Black"
CAPTION_FONT = "Arial"
RED_BGR = "&H003A25D3"          # #d3253a - ASS colours are &HAABBGGRR


def filter_path(path: str) -> str:
    """A path as it must be written INSIDE an ffmpeg filter argument
    (same rules as autoreel.clip_maker.escape_filter_path)."""
    path = os.path.abspath(path).replace("\\", "/").replace(":", r"\:")
    return path.replace("'", r"\'").replace("[", r"\[").replace("]", r"\]")


def _t(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def _header(styles: str) -> str:
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
            "Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"{styles}\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


PIECE_STYLES = (
    f"Style: Caption,{CAPTION_FONT},60,&H00FFFFFF,&H00000000,&H00000000,-1,0,1,5,2,2,120,120,70,1\n"
    # BorderStyle 3: an opaque box in the outline colour - a red tag.
    f"Style: Topic,{FONT},44,&H00FFFFFF,{RED_BGR},&H00000000,0,0,3,14,0,7,70,70,60,1\n")

CARD_STYLES = (
    f"Style: Big,{FONT},150,&H00FFFFFF,&H00000000,&H64000000,0,0,1,6,4,5,80,80,0,1\n"
    f"Style: Sub,{FONT},52,&H00FFFFFF,{RED_BGR},&H00000000,0,0,3,14,0,2,80,80,250,1\n"
    f"Style: Name,{CAPTION_FONT},40,&H00DDDDDD,&H00000000,&H00000000,-1,0,1,3,0,8,80,80,180,1\n")


def piece_ass(path: str, segments: Sequence[dict], start: float, end: float,
              title: str = "") -> Optional[str]:
    """Captions (censored, word by word) and the topic tag for one moment
    of the recap, timed from the moment's own start. None when there is
    nothing to put on screen."""
    from autoreel.captions import censor_words, group_words, words_in_range, _word_lines
    from autoreel.safe_text import clean_title

    length = max(0.0, end - start)
    lines = []
    title = clean_title(title or "", fallback="") if title else ""
    if title and length > 2:
        stop = min(TITLE_OUT_S, length - 0.2)
        lines.append(f"Dialogue: 1,{_t(TITLE_IN_S)},{_t(stop)},Topic,,0,0,0,,"
                     f"{{\\fad(250,300)}}{_esc(title.upper())}")
    for phrase in group_words(censor_words(words_in_range(segments, start, end))):
        if phrase.end <= phrase.start:
            continue
        for begin, stop, text in _word_lines(phrase, True):
            lines.append(f"Dialogue: 0,{_t(begin)},{_t(stop)},Caption,,0,0,0,,{text}")
    if not lines:
        return None
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_header(PIECE_STYLES) + "\n".join(lines) + "\n")
    return path


END_CARD_S = 7.0


def card_ass(path: str, heading: str, sub: str, name: str = "STACKSWOPO",
             seconds: float = CARD_S) -> str:
    """The opening card - or, with END_CARD_S, the closing one that sends
    viewers to the full stream."""
    from autoreel.safe_text import clean_title

    heading = clean_title(heading, fallback="BEST MOMENTS") or "BEST MOMENTS"
    lines = [
        f"Dialogue: 0,{_t(0)},{_t(seconds)},Name,,0,0,0,,{{\\fad(200,0)}}{_esc(name.upper())}",
        f"Dialogue: 0,{_t(0)},{_t(seconds)},Big,,0,0,0,,{{\\fad(200,0)}}{_esc(heading.upper())}",
        f"Dialogue: 0,{_t(0.25)},{_t(seconds)},Sub,,0,0,0,,{{\\fad(250,0)}}{_esc(sub.upper())}",
    ]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_header(CARD_STYLES) + "\n".join(lines) + "\n")
    return path


TOPIC_SYSTEM = (
    "You write the on-screen topic tag for each moment of a best-moments recap of "
    "Stackswopo's GTA roleplay stream. He is the main character of every moment, "
    "whatever character he is playing. For each numbered moment, write what happens in "
    "3 to 7 plain words, like a TV chyron: 'Stacks negotiates with the medics', "
    "'Pulled over again', 'The pastor wants his tithe'. Clean enough for a TV guide: "
    "no swears, no slurs, no body parts, nothing sexual, no drugs, never mock how anyone "
    "looks - if the moment is only about one of those, describe the situation instead "
    "('Barbershop argument'). No quotes, no emoji, no hashtags. Reply as JSON: "
    '{"titles": ["...", "..."]} with exactly one title per moment, in order.')


def topic_titles(texts: Sequence[str], ask: Optional[Callable[[str, str], str]] = None) -> List[str]:
    """One topic tag per moment from what is said in it. "" for a moment
    the model could not title - no tag beats a wrong one."""
    if not texts:
        return []
    prompt = "\n\n".join(f"[{i + 1}] {' '.join(str(t).split())[:600]}"
                         for i, t in enumerate(texts))
    reply = ""
    try:
        if ask is None:
            from autoreel import local_llm

            if not local_llm.ready():
                return [""] * len(texts)
            reply, _why = local_llm.chat(TOPIC_SYSTEM, prompt, json_reply=True)
        else:
            reply = ask(TOPIC_SYSTEM, prompt)
        titles = json.loads(reply or "{}").get("titles") or []
    except Exception:
        return [""] * len(texts)
    from autoreel.safe_text import clean_title

    out = []
    for i in range(len(texts)):
        raw = str(titles[i]).strip().strip('"') if i < len(titles) else ""
        words = raw.split()
        clean = clean_title(raw, fallback="") if 2 <= len(words) <= 9 else ""
        # A tag that needed a word starred is not a tag for a channel with a
        # strike ("Stacks discusses d*** inflation" was the first try): no tag.
        # Nor one about a body, sex, drugs or what group someone belongs to.
        lowered = {w.strip(".,!?'\"").lower() for w in clean.split()}
        if "*" in clean or clean != raw or lowered & TAG_BLOCK:
            clean = ""
        out.append(clean)
    return out


TAG_BLOCK = frozenset({
    "sex", "sexual", "sexy", "naked", "nude", "dick", "penis", "booty", "butt", "ass",
    "boobs", "titties", "body", "fat", "ugly", "drug", "drugs", "weed", "crack", "cocaine",
    "coke", "meth", "high", "gay", "lesbian", "trans", "transgender", "black", "white",
    "mexican", "asian", "jewish", "muslim", "race", "racial", "racist", "slur", "slurs",
})


FIT = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
AUDIO_OUT = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def _run(args: List[str], timeout: int = 1800) -> bool:
    done = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True,
                          text=True, timeout=timeout)
    return done.returncode == 0


def still(source: str, at: float, png: str) -> bool:
    return _run(["-ss", f"{max(0.0, at):.3f}", "-i", source, "-frames:v", "1", "-vf", FIT, png],
                timeout=120) and os.path.exists(png)


def last_frame(part: str, png: str) -> bool:
    """The part's final frame. Seeked by its measured length: -sseof on an
    MPEG-TS part came back empty (the first moment slid in over black)."""
    try:
        length = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             part], capture_output=True, text=True, timeout=60).stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return _run(["-ss", f"{max(0.0, length - 0.1):.3f}", "-i", part, "-frames:v", "1",
                 "-update", "1", png], timeout=120) and os.path.exists(png)


def card_frame(still_png: str, ass_path: str, png: str) -> bool:
    """The card exactly as it looks at its end, for the first moment to
    slide in over."""
    vf = (f"boxblur=24:2,eq=brightness=-0.18:saturation=1.15,"
          f"subtitles='{filter_path(ass_path)}'")
    return _run(["-loop", "1", "-t", "3", "-i", still_png, "-vf", vf, "-ss", "2.9",
                 "-frames:v", "1", "-update", "1", png], timeout=120) and os.path.exists(png)


def render_card(out_ts: str, still_png: str, ass_path: str, video_codec: List[str],
                seconds: float = CARD_S) -> bool:
    """`seconds` of the still, blurred and darkened, with the card's text."""
    vf = (f"boxblur=24:2,eq=brightness=-0.18:saturation=1.15,"
          f"subtitles='{filter_path(ass_path)}',fps=60,format=yuv420p")
    return _run(["-loop", "1", "-framerate", "60", "-t", f"{seconds}", "-i", still_png,
                 "-f", "lavfi", "-t", f"{seconds}", "-i", "anullsrc=r=48000:cl=stereo",
                 "-vf", vf, *video_codec, *AUDIO_OUT, "-shortest", "-f", "mpegts", out_ts],
                timeout=300)


def piece_args(source: str, start: float, end: float, out_ts: str, fg_chain: str,
               video_codec: List[str], ass_path: Optional[str] = None,
               behind_png: Optional[str] = None) -> List[str]:
    """ffmpeg arguments for one moment that slides in from the right over
    `behind_png` (the previous moment's last frame; black for the first),
    with its captions and topic tag burned in."""
    behind = (["-loop", "1", "-framerate", "60", "-i", behind_png] if behind_png
              else ["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r=60"])
    subs = f",subtitles='{filter_path(ass_path)}'" if ass_path else ""
    graph = (f"[0:v]{fg_chain}[fg];[1:v]scale={W}:{H},format=yuv420p[bg];"
             f"[bg][fg]overlay=x='if(lt(t,{SLIDE_S}),W*pow(1-t/{SLIDE_S},2),0)':y=0:"
             f"eval=frame:shortest=1{subs}[v]")
    return ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", source, *behind,
            "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0", *video_codec,
            "-af", "aresample=48000,afade=t=in:d=0.15", *AUDIO_OUT, "-f", "mpegts", out_ts]

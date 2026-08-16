"""
Burned-in captions from word timings.

Short-form video is watched muted by default, so captions are not a
garnish - they are how most of the audience receives the words at all.
The transcript that produces them already exists: the censor pass caches
word-level timings for every video, so captions cost a file write rather
than a second transcription.

ASS rather than SRT, because SRT carries no styling: the player picks the
font and size, and ffmpeg's default for a burned-in SRT is small,
serif-ish, and bottom-anchored - unreadable on a phone over gameplay.
ASS puts the styling in the file, so what renders is what was chosen.

Words are grouped into short phrases rather than shown one at a time.
One-word-at-a-time reads as a gimmick and forces the eye to re-fixate on
every word; a 3-5 word phrase held for its own duration tracks how people
actually read.

TWO STYLES
----------
`block` holds the phrase, plain white. Correct, readable, and completely
inert - which is why captions got turned off here: a static white slab
over the picture looks like a subtitle track rather than part of the
video.

`word` holds the same phrase and lights up each word AS IT IS SAID. The
phrase stays on screen the whole time, so nothing has to be re-read; only
the colour moves. That is the format short-form has settled on, and the
reason is retention rather than fashion - the highlight gives the eye a
reason to stay on the frame between one sentence and the next.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional

# 1080x1920. Captions sit above the lower third, clear of the UI overlays
# every platform puts along the bottom edge (Reels' caption/audio strip,
# Shorts' subscribe bar) - text placed at the true bottom gets covered.
PLAY_RES_X = 1080
PLAY_RES_Y = 1920
MARGIN_V = 420

DEFAULT_FONT = "Arial"

# Sized so a full-length line FITS. The frame is 1080 wide with 80px
# margins each side, leaving 920px. Arial Bold at this size averages
# about 0.58em per uppercase character, so the char limit below is what
# actually keeps a line inside the frame - the two numbers are a pair
# and changing one without the other pushes text off both edges.
#
# It did exactly that: 28 characters at 84px is roughly 1400px of text
# in a 920px space, and every clip went out with the first and last
# words sliced off.
DEFAULT_FONT_SIZE = 62
DEFAULT_MAX_WORDS = 4
DEFAULT_MAX_CHARS = 20

# Width one uppercase character takes, as a fraction of font size, for
# Arial Bold. Used by fits_in_frame below.
_CHAR_WIDTH_EM = 0.58

# Left plus right margin in the style line.
_SIDE_MARGINS = 160


def fits_in_frame(max_chars: int = DEFAULT_MAX_CHARS,
                  font_size: int = DEFAULT_FONT_SIZE) -> bool:
    """Whether the longest allowed line fits between the margins."""
    return max_chars * font_size * _CHAR_WIDTH_EM <= (
        PLAY_RES_X - _SIDE_MARGINS)


# A phrase held longer than this reads as a stuck caption.
DEFAULT_MAX_PHRASE_S = 3.0
# A gap this long between words is a new thought, so break the phrase.
DEFAULT_GAP_S = 0.6


# ASS colours are &HBBGGRR - byte-reversed from the hex everyone knows.
# This is bright yellow (RGB 255,255,0), which holds up over both a
# bright skybox and a dark room.
HIGHLIGHT_COLOUR = "&H0000FFFF&"
PLAIN_COLOUR = "&H00FFFFFF&"
# The spoken word is drawn slightly larger as well as recoloured, so the
# emphasis survives on a phone screen and for anyone colourblind.
HIGHLIGHT_SCALE = 112

STYLE_BLOCK = "block"
STYLE_WORD = "word"
DEFAULT_STYLE = STYLE_WORD


@dataclass
class Phrase:
    start: float
    end: float
    text: str
    # (text, start, end) per word, for the highlighted style. Kept beside
    # the joined text rather than re-split from it: punctuation and
    # spacing make splitting back into timed words lossy.
    words: list = None

    def __post_init__(self):
        if self.words is None:
            self.words = []


def _clean(word: str) -> str:
    return (word or "").strip()


# The audio pass mutes flagged speech. The caption pass used to print the
# transcript verbatim underneath it - so a word that was silenced in the
# audio was rendered in yellow, forty-eight point, in the middle of the
# frame, and auto-posted. Censoring one channel and broadcasting the
# other is worse than doing neither, because the mute makes it look
# handled.
#
# Masked rather than dropped: a missing word makes the line read wrong,
# and "sh*t" is the convention short-form already uses - it is what this
# channel's own titles do.
_KEEP_LEADING = 1


def mask_word(text: str) -> str:
    """f*** - first letter kept, punctuation kept, the rest starred."""
    leading = ""
    trailing = ""
    core = text
    while core and not core[0].isalnum():
        leading, core = leading + core[0], core[1:]
    while core and not core[-1].isalnum():
        trailing, core = core[-1] + trailing, core[:-1]
    if not core:
        return text
    if len(core) <= _KEEP_LEADING:
        return leading + "*" * len(core) + trailing
    return leading + core[:_KEEP_LEADING] + "*" * (len(core) - _KEEP_LEADING) + trailing


def censor_words(words: Iterable[dict], checker=None) -> list:
    """The same word list, with anything the compliance pass flags masked.

    `checker` is a ComplianceChecker; built with defaults when not given,
    so a caller cannot forget to censor by omitting an argument. If the
    compliance module cannot be loaded for any reason the words are
    returned untouched - captions failing closed would mean no clips at
    all, and the audio mute still stands.
    """
    words = list(words)
    if checker is None:
        from .compliance import ComplianceEngine

        # Deliberately NOT wrapped in try/except. An import that fails
        # here would mean captions render uncensored, and a silent
        # fallback to "print it anyway" is how a slur reached a posted
        # clip in the first place. Loud failure is the safe direction.
        checker = ComplianceEngine()

    censored = []
    for word in words:
        text = _clean(word.get("word"))
        try:
            flagged = bool(text) and checker._flag_reason(text) is not None
        except Exception:
            flagged = False
        if flagged:
            word = dict(word)
            word["word"] = mask_word(text)
        censored.append(word)
    return censored


def group_words(words: Iterable[dict],
                max_words: int = DEFAULT_MAX_WORDS,
                max_chars: int = DEFAULT_MAX_CHARS,
                max_seconds: float = DEFAULT_MAX_PHRASE_S,
                gap_seconds: float = DEFAULT_GAP_S) -> list:
    """Group word timings into readable phrases.

    Breaks on any of: word count, line length, elapsed time, a pause, or
    sentence-ending punctuation. Several limits rather than one because
    they catch different things - four short words and four long ones
    occupy very different amounts of screen.
    """
    phrases: list = []
    current: list = []
    timed: list = []
    start = 0.0
    last_end = 0.0

    def flush() -> None:
        nonlocal current, timed
        if current:
            phrases.append(Phrase(start, last_end, " ".join(current),
                                  list(timed)))
            current = []
            timed = []

    for word in words:
        text = _clean(word.get("word"))
        if not text:
            continue
        try:
            w_start = float(word["start"])
            w_end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue

        if current and (w_start - last_end) >= gap_seconds:
            flush()
        if not current:
            start = w_start

        candidate = " ".join(current + [text])
        too_long = len(candidate) > max_chars and current
        too_many = len(current) >= max_words
        too_slow = current and (w_end - start) > max_seconds
        if too_long or too_many or too_slow:
            flush()
            start = w_start

        current.append(text)
        timed.append((text, w_start, w_end))
        last_end = w_end

        # Sentence end: break here so a phrase never straddles two of them.
        if text.endswith((".", "!", "?")):
            flush()

    flush()
    return phrases


def words_in_range(segments: Iterable[dict], start: float, end: float,
                   shift: float = 0.0) -> list:
    """Word timings inside [start, end), rebased so the clip starts at 0.

    Rebasing here rather than at render time is what lets the caption file
    be handed straight to ffmpeg alongside a trimmed clip - the timings
    already match the trimmed video.

    `shift` is added to every word, and is how a transcript that writes
    words down slightly beside the sound of them gets corrected. Whisper
    is accurate to a tenth of a second or so, which is enough to read as
    wrong on a caption that lights up word by word. Measured per clip by
    clip_sync.alignment_for; 0.0 means it found nothing worth fixing,
    which is the normal answer.
    """
    start -= shift
    end -= shift
    out = []
    for segment in segments or ():
        for word in (segment.get("words") or ()):
            try:
                w_start = float(word["start"])
                w_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if w_end <= start or w_start >= end:
                continue
            out.append({
                "word": word.get("word", ""),
                # Clamp: a word straddling the cut would otherwise render
                # at a negative time and be dropped by the renderer.
                "start": max(0.0, w_start - start),
                "end": min(end, w_end) - start,
            })
    return out


def _ass_time(seconds: float) -> str:
    """ASS wants H:MM:SS.cc, centiseconds, single-digit hour."""
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def _ass_escape(text: str) -> str:
    # A literal brace opens an ASS override block; a backslash starts an
    # escape. Both appear in ordinary speech transcripts.
    return (text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                .replace("\n", " "))


def _word_lines(phrase: Phrase, uppercase: bool) -> list:
    """One Dialogue per word: the whole phrase, that word lit up.

    The phrase stays on screen for its full length and only the colour
    moves, so nothing has to be re-read. Each word's slot runs to the
    START of the next rather than to its own end - the silence between
    two words belongs to the one just said, and ending on the word's own
    end leaves the caption blinking out in every gap.
    """
    words = phrase.words or []
    if not words:
        text = phrase.text.upper() if uppercase else phrase.text
        return [(phrase.start, phrase.end, _ass_escape(text))]

    rendered = [(_ass_escape(w.upper() if uppercase else w), a, b)
                for w, a, b in words]
    lines = []
    for index, (_text, w_start, _w_end) in enumerate(rendered):
        stop = (rendered[index + 1][1] if index + 1 < len(rendered)
                else max(phrase.end, _w_end))
        if stop <= w_start:
            continue
        parts = []
        for other, (text, _a, _b) in enumerate(rendered):
            if other == index:
                parts.append(
                    f"{{\\c{HIGHLIGHT_COLOUR}\\fscx{HIGHLIGHT_SCALE}"
                    f"\\fscy{HIGHLIGHT_SCALE}}}{text}"
                    f"{{\\c{PLAIN_COLOUR}\\fscx100\\fscy100}}")
            else:
                parts.append(text)
        lines.append((w_start, stop, " ".join(parts)))
    return lines


def build_ass(phrases: Iterable[Phrase], font: str = DEFAULT_FONT,
              font_size: int = DEFAULT_FONT_SIZE,
              margin_v: int = MARGIN_V,
              style: str = DEFAULT_STYLE,
              uppercase: bool = True) -> str:
    """An ASS subtitle file: white text, heavy black outline, centred.

    Outline plus shadow rather than a background box: gameplay footage
    changes brightness constantly, and an outline stays legible over both
    a bright skybox and a dark interior without covering the picture.
    """
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_RES_X}
PlayResY: {PLAY_RES_Y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{font_size},&H00FFFFFF,&H00000000,&H00000000,-1,0,1,6,3,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for phrase in phrases:
        if phrase.end <= phrase.start:
            continue
        if style == STYLE_WORD:
            events = _word_lines(phrase, uppercase)
        else:
            text = phrase.text.upper() if uppercase else phrase.text
            events = [(phrase.start, phrase.end, _ass_escape(text))]
        for begin, stop, text in events:
            lines.append(
                f"Dialogue: 0,{_ass_time(begin)},{_ass_time(stop)},"
                f"Caption,,0,0,0,,{text}")
    return header + "\n".join(lines) + ("\n" if lines else "")


def write_ass(path: str, phrases: Iterable[Phrase], **style) -> str:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_ass(phrases, **style))
    return path


def caption_file_for_clip(path: str, segments: Iterable[dict],
                          start: float, end: float,
                          shift: float = 0.0,
                          **style) -> Optional[str]:
    """Write captions for one clip window. None if there is nothing to say.

    Censoring happens HERE, not at the call site, so there is no way to
    render a caption track without it. The audio pass already mutes this
    speech; printing it underneath in yellow was the bug.
    """
    phrases = group_words(censor_words(
        words_in_range(segments, start, end, shift)))
    if not phrases:
        return None
    return write_ass(path, phrases, **style)

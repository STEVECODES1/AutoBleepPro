"""The last check on every title, description and caption before it is public.

Why it exists: on 10-16 the weekly video went up as
"Stackswopo - BEST OF THE WEEK (10-09-26 to )" - one missing date left a
dangling "to )" in a public title. Before that, mojibake ("Â·") reached
13 Instagram captions. Each bug was fixed where it started; this is the
net under all of them. Whatever builds the text, what goes out is tidied
here first.

It removes only what is never meant to be seen - mojibake, empty
brackets, half-filled ranges, unfilled template tokens, a recorder's
file-name timestamp, doubled separators and stray spaces - and leaves
words, emoji, links and line breaks alone. When it changes something it
prints what, so the builder that made the text gets fixed too. It never
raises: a check that crashed an upload would be worse than the typo.

    python tools/metadata_audit.py      list what is wrong with what is already up
"""
import re

try:
    from utils.unmojibake import fix as _unmojibake
except ImportError:  # imported with utils/ itself on sys.path (tools/)
    from unmojibake import fix as _unmojibake

_SEP = "-–—|·•"            # - – — | · •
_TOKENS = re.compile(r"\[(?:STREAM TITLE|TITLE|DATE|CAPTION)\]|\{[a-z_][a-z0-9_]*\}")
_NULLS = re.compile(r"[ \t]*[(\[][ \t]*(?:None|nan|NaN|null|undefined)[ \t]*[)\]]")
# "(10-09-26 to )", "( to 10-15-26)", "(Part 2 - )". "to" only next to a
# number, so a real "(how to)" is left alone.
_OPEN_END = re.compile(r"\([ \t]*([^()\n]*?\d)[ \t]+to[ \t]*\)"
                       r"|\([ \t]*([^()\n]*?\S)[ \t]+[-–—][ \t]*\)")
_OPEN_START = re.compile(r"\([ \t]*to[ \t]+(\d[^()\n]*?)[ \t]*\)"
                         r"|\([ \t]*[-–—][ \t]+([^()\n]*?\S)[ \t]*\)")
_DATE_TO_END = re.compile(r"(\d{1,2}-\d{1,2}-\d{2,4})[ \t]+to[ \t]*$", re.M)
_EMPTY = re.compile(r"[ \t]*[(\[{][ \t" + re.escape(_SEP) + r":,/]*[)\]}]")
# A recorder's start time glued to a name: "THE RETURN 2026-10-09 14:55".
# Only right before a line end, quote or bracket - a date a person wrote
# into a sentence is left alone. STAMP (no lead space) is shared with the
# dedup checks so both sides of a title comparison drop it the same way.
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ _T]\d{2}[-:_]\d{2}(?:[-:_]\d{2})?"
                   r"(?=[ \t]*(?:$|[\"'”)\](]))"
                   r"|\d{4}-\d{2}-\d{2}(?=[\"”])", re.M)    # "FINGER 2026-10-05"
_STAMP = re.compile(r"(?<=\S)[ \t]+(?:" + STAMP.pattern + ")", re.M)
_DOUBLE_SEP = re.compile(r"[ \t]+([" + re.escape(_SEP) + r"])(?:[ \t]+[" + re.escape(_SEP) + r"])+(?=[ \t])")
_TRAIL_SEP = re.compile(r"(?<=\S)[ \t]+[" + re.escape(_SEP) + r"/]+[ \t]*$", re.M)
_LONE_BULLET = re.compile(r"^[ \t]*[" + re.escape(_SEP) + r"][ \t]*$", re.M)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FILE_EXT = re.compile(r"(?<=\w)\.(?:mp4|mkv|ts|flv|mov|webm)$", re.I)
_LINK_LABEL = re.compile(r"(?i)(rumble|full stream|watch|uncut|links?)\b[^\n]*:[ \t]*$")


def _keep(m):
    return "(" + (m.group(1) or m.group(2)) + ")"


def _spaces(s):
    s = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", s)
    s = re.sub(r"[ \t]+(?=[,)\]]|\.(?:\s|$))", "", s)
    s = re.sub(r"([(\[])[ \t]+", r"\1", s)
    return re.sub(r"[ \t]+$", "", s, flags=re.M)


# (name, rule) in the order they run. Each rule is safe on clean text:
# it changes nothing that a person meant to write.
RULES = [
    ("garbled characters (mojibake)", _unmojibake),
    ("control characters", lambda s: _CONTROL.sub("", s.replace("\r\n", "\n"))),
    ("unfilled template token", lambda s: _TOKENS.sub("", s)),
    ("None/nan where a value should be", lambda s: _NULLS.sub("", s)),
    ("half-filled range like '(10-09-26 to )'",
     lambda s: _DATE_TO_END.sub(r"\1", _OPEN_START.sub(_keep, _OPEN_END.sub(_keep, s)))),
    ("empty brackets", lambda s: _EMPTY.sub("", s)),
    ("empty quotes", lambda s: re.sub("[ \t]*(?:\"\"|“”)", "", s)),
    ("recording file-name timestamp", lambda s: _STAMP.sub("", s)),
    ("doubled separator", lambda s: _DOUBLE_SEP.sub(r" \1", s)),
    ("dangling separator at a line end", lambda s: _TRAIL_SEP.sub("", s)),
    ("line with only a bullet", lambda s: _LONE_BULLET.sub("", s)),
    ("stray spaces", _spaces),
    ("extra blank lines", lambda s: re.sub(r"\n{3,}", "\n\n", s).strip()),
]


def _say(kind, before, after):
    try:
        if before.strip() != after:
            print(f"[TextGuard] tidied {kind}: {ascii(before[:140])} -> {ascii(after[:140])}")
    except Exception:
        pass


# Titles skip the timestamp rule. A title is also an identity: the retry
# dedup matches it, and two untitled streams on one day differ ONLY by
# their start time. The title builders drop the stamp themselves.
_NOT_IN_TITLES = ("recording file-name timestamp",)


def _apply(s, skip=()):
    for name, rule in RULES:
        if name not in skip:
            s = rule(s)
    return s


def text(s, kind="text"):
    """Captions and any multi-line text: tidied, line breaks kept."""
    if not isinstance(s, str) or not s:
        return s
    try:
        out = _apply(s)
    except Exception as exc:  # never let the check stop a post
        print(f"[TextGuard] skipped ({type(exc).__name__}: {exc})")
        try:
            return _unmojibake(s)
        except Exception:
            return s
    _say(kind, s, out)
    return out


def _one_line(s):
    s = re.sub(r"\s+", " ", s).strip()
    s = _FILE_EXT.sub("", s)
    return s.strip(" \t" + _SEP + ":,/")


def title(s, fallback="", limit=100, youtube=True):
    """A title: one line, no dangling bits, cut at a word to `limit`.
    YouTube refuses '<' and '>' in a title, so they become '‹' '›'."""
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    try:
        out = _one_line(_apply(s, _NOT_IN_TITLES))
        if youtube:
            out = out.replace("<", "‹").replace(">", "›")
        if limit and len(out) > limit:
            cut = out[:limit + 1].rsplit(" ", 1)[0] if " " in out[:limit + 1] else out[:limit]
            out = cut[:limit].rstrip(" \t" + _SEP + ":,/")
    except Exception as exc:
        print(f"[TextGuard] skipped title ({type(exc).__name__}: {exc})")
        out = s.strip()
    if not out and fallback:
        out = title(fallback, "", limit, youtube)
    _say("title", s, out)
    return out


def youtube_description(s, limit_bytes=5000):
    """A YouTube description: tidied, '<' '>' swapped (YouTube refuses
    them), and cut at a line break to fit YouTube's 5000-byte cap."""
    out = text(s, "description")
    if not isinstance(out, str):
        return out
    out = out.replace("<", "‹").replace(">", "›")
    if len(out.encode("utf-8")) > limit_bytes:
        raw = out.encode("utf-8")[:limit_bytes].decode("utf-8", "ignore")
        out = raw.rsplit("\n", 1)[0] if "\n" in raw else raw
    return out.rstrip()


def problems(s, is_title=False, youtube=False):
    """What the guard would change in `s`, by name - for audits."""
    if not isinstance(s, str):
        return []
    found = []
    for name, rule in RULES:
        if is_title and name in _NOT_IN_TITLES:
            continue
        try:
            if name in ("stray spaces", "extra blank lines"):
                changed = rule(s) != s.strip() and rule(s).strip() != s.strip()
            else:
                changed = rule(s) != s
        except Exception:
            changed = False
        if changed:
            found.append(name)
    if "Â" in s or "â€" in s:
        if "garbled characters (mojibake)" not in found:
            found.append("garbled characters (mojibake)")
    if is_title:
        if not s.strip():
            found.append("empty title")
        if _FILE_EXT.search(s.strip()):
            found.append("video file name in the title")
        if len(s) > 100:
            found.append("title over 100 characters")
        if re.search(r"(^\s*[" + re.escape(_SEP) + r":,/])|([" + re.escape(_SEP) + r":,/]\s*$)", s):
            found.append("title starts or ends with a separator")
    else:
        lines = s.split("\n")
        for i, line in enumerate(lines):
            if _LINK_LABEL.search(line):
                nxt = next((x for x in lines[i + 1:] if x.strip()), "")
                if "http" not in nxt and not re.search(r"https?://|@\w", line):
                    found.append(f"link label with no link: {line.strip()[:60]!r}")
    if youtube and re.search(r"[<>]", s):
        found.append("'<' or '>' (YouTube refuses them)")
    return found

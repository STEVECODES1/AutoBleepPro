"""
Colour for the uploader's terminal output.

WHY THIS EXISTS
---------------
A run prints thousands of lines. On a real night the important ones -
"[Rumble] UPLOAD FAILED", "circuit breaker open", "YouTube sign-in has
expired" - sat in the middle of hundreds of identical [Timing] and
[Queue] lines in exactly the same white text, and were missed for hours.
Colour is not decoration here: it is the difference between noticing a
dead upload at 05:47 and noticing it at 09:29.

WHAT GETS WHICH COLOUR
----------------------
Red     something failed and needs a person
Yellow  something was skipped, queued or retried - working as designed,
        but not what was asked for
Green   something landed
Cyan    the section headings a run is read by
Dim     timing, cleanup, queue depth: true, and never the thing you are
        looking for

HOW IT DEGRADES
---------------
Every helper returns plain text when colour is not available, so a
caller never has to ask. Colour is off when output is redirected to a
file (a .log full of escape codes is worse than no colour), when NO_COLOR
is set - the de-facto standard - or when the terminal says it is dumb.
FORCE_COLOR=1 turns it back on for a terminal that is lying.

Windows needs one call to enable ANSI processing in the console, which
`enable()` does. Python 3.13+ and Windows Terminal both do it already;
the old conhost does not, and without it every line starts with a
literal escape sequence.
"""

from __future__ import annotations

import os
import sys

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"

BRIGHT_RED = "\033[91m"
BRIGHT_GREEN = "\033[92m"
BRIGHT_YELLOW = "\033[93m"
BRIGHT_CYAN = "\033[96m"

_enabled: bool | None = None


def _detect(stream=None) -> bool:
    """Should colour be used for this stream right now?"""
    if (os.environ.get("FORCE_COLOR") or "").strip() not in ("", "0"):
        return True
    if os.environ.get("NO_COLOR") is not None:
        return False
    if (os.environ.get("TERM") or "").strip().lower() == "dumb":
        return False
    stream = stream or sys.stdout
    try:
        # Redirected to a file or a pipe: a log full of "\033[31m" is
        # harder to read than one with no colour at all.
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def enable(stream=None) -> bool:
    """Turn colour on if this terminal can take it. Returns whether it did.

    Safe to call more than once, and safe to never call - the helpers
    below detect on first use.
    """
    global _enabled
    if sys.platform == "win32":
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING on the console handle. Without
        # it the old Windows console prints the escape codes literally.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            for handle in (-11, -12):      # stdout, stderr
                mode = ctypes.c_uint32()
                if kernel32.GetConsoleMode(kernel32.GetStdHandle(handle),
                                           ctypes.byref(mode)):
                    kernel32.SetConsoleMode(
                        kernel32.GetStdHandle(handle), mode.value | 0x0004)
        except Exception:
            # Not a console, no ctypes, a locked-down host: colour simply
            # stays off. This must never be why a run does not start.
            pass
    _enabled = _detect(stream)
    return _enabled


def on() -> bool:
    if _enabled is None:
        enable()
    return bool(_enabled)


def paint(text: str, *codes: str) -> str:
    """`text` wrapped in `codes`, or unchanged when colour is off."""
    if not text or not on() or not codes:
        return text
    return f"{''.join(codes)}{text}{RESET}"


# ── the vocabulary the rest of the program uses ────────────────────────
#
# Named for what the line MEANS, not for a colour. A caller writing
# term.failure(...) keeps working if red turns out to be the wrong red;
# a caller writing term.red(...) has to be found and edited.

def failure(text: str) -> str:
    """Something broke and a person has to do something."""
    return paint(text, BOLD, BRIGHT_RED)


def error(text: str) -> str:
    """A failure, stated without shouting."""
    return paint(text, RED)


def warning(text: str) -> str:
    """Skipped, queued, retried, degraded - not broken."""
    return paint(text, YELLOW)


def success(text: str) -> str:
    """It landed."""
    return paint(text, BRIGHT_GREEN)


def heading(text: str) -> str:
    """The lines a run is skimmed by."""
    return paint(text, BOLD, BRIGHT_CYAN)


def detail(text: str) -> str:
    """True, and never the thing being looked for."""
    return paint(text, DIM)


def highlight(text: str) -> str:
    """A value worth the eye stopping on - a URL, a count, a title."""
    return paint(text, BOLD)


# ── automatic colouring for lines that already exist ───────────────────

# The uploader prints thousands of prefixed lines and rewriting every
# print() would be a very large diff for a cosmetic change. `line()`
# takes a finished line and colours it by what it says, so one call at
# the print site covers all of them.
_FAILURE_MARKERS = (
    "UPLOAD FAILED", "FAILED:", "ERROR", "!! HIGH RISK", "Traceback",
    "could not", "is not reachable", "not signed into",
)
_WARNING_MARKERS = (
    "skipped", "circuit breaker", "queued", "retrying", "WARNING",
    "expired", "not configured", "not installed", "disabled in config",
    "still hasn't", "Nothing landed",
    # The recorder's own vocabulary. A wall of 403s scrolling past in
    # plain white looks exactly like normal download output; it is the
    # sign a manifest has gone stale and the recording is about to
    # restart.
    "Got error:", "Retrying fragment", "Recording dropped", "reconnecting",
    "gone stale", "being refused",
)
_SUCCESS_MARKERS = (
    "uploaded successfully", "upload complete", "Posted to", "Posted the",
    "landed", "-> https://", "Attached to Chrome", "Live - recording started",
)
_DETAIL_PREFIXES = ("[Timing]", "[Cleanup]", "[Queue]", "[Check]")

# The recorder times every line, so it has no prefix to match on. These
# are its "nothing is happening" lines - true, and never what is being
# looked for in a window that has been open for eleven hours.
_DETAIL_MARKERS = ("Still watching", "Nothing live", "Waiting for",
                   "Holding the machine awake")


def line(text: str) -> str:
    """Colour a finished output line by what it says.

    Checked failure-first: a line saying both "failed" and "retrying" is
    a failure that will be retried, and the failure is the part worth
    seeing.
    """
    if not text or not on():
        return text
    stripped = text.strip()
    if any(m in text for m in _FAILURE_MARKERS):
        return failure(text)
    if any(m in text for m in _WARNING_MARKERS):
        return warning(text)
    if any(m in text for m in _SUCCESS_MARKERS):
        # A Discord receipt reading "1 of 2 landed, 1 failed" matches a
        # success marker and is not a success. Green on a line that says
        # something failed is worse than no colour: it actively tells
        # you not to look.
        if "fail" in text.lower():
            return warning(text)
        return success(text)
    if stripped.startswith(_DETAIL_PREFIXES) or any(
            m in text for m in _DETAIL_MARKERS):
        return detail(text)
    if stripped.startswith("=====") or stripped.startswith("-----"):
        return heading(text)
    return text


# ── one hook for the whole program ─────────────────────────────────────

class _ColouringStream:
    """Wraps a stream and colours each COMPLETE line as it is written.

    A wrapper rather than editing several thousand print() calls. It
    buffers partial writes and only colours at a newline, so a progress
    line written with end="" or \\r - "[YouTube] Uploading... 10%" - is
    passed straight through and keeps redrawing in place. Colouring a
    fragment would put a reset code in the middle of the line and leave
    the rest of it uncoloured anyway.
    """

    def __init__(self, stream):
        self._stream = stream
        self._buffer = ""

    def write(self, text):
        if not text:
            return 0
        written = len(text)
        self._buffer += text
        while "\n" in self._buffer:
            done, self._buffer = self._buffer.split("\n", 1)
            self._stream.write(line(done) + "\n")
        if self._buffer:
            # No newline yet. Flush it uncoloured so progress readouts
            # and input() prompts still appear immediately instead of
            # sitting in this buffer until something ends a line.
            self._stream.write(self._buffer)
            self._buffer = ""
        return written

    def flush(self):
        return self._stream.flush()

    def isatty(self):
        try:
            return self._stream.isatty()
        except (AttributeError, ValueError):
            return False

    def __getattr__(self, name):
        return getattr(self._stream, name)


def colourise_stdout() -> bool:
    """Colour everything this program prints. Returns whether it did.

    Call once, early. Does nothing when colour is off, and never wraps
    twice, so a second call from a re-entrant path is harmless.
    """
    if not enable():
        return False
    if isinstance(sys.stdout, _ColouringStream):
        return True
    sys.stdout = _ColouringStream(sys.stdout)
    return True

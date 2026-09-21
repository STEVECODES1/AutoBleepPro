"""
The important lines have to be findable.

A run prints thousands of lines. On the night of 2026-09-21 the Rumble
upload died at 05:47 and was noticed at 09:29, because
"[Rumble] UPLOAD FAILED" was the same white text as the two hundred
"[Timing]" lines around it. That is what this is for.

Everything here is about the two properties that make colour safe to
turn on by default: it degrades to plain text whenever the output is not
a terminal a person is watching, and it never mangles a line.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

import io  # noqa: E402

import pytest  # noqa: E402

from utils import term  # noqa: E402


@pytest.fixture(autouse=True)
def _forced_on(monkeypatch):
    """Colour on, deterministically, without needing a real terminal."""
    monkeypatch.setattr(term, "_enabled", True)
    yield
    monkeypatch.setattr(term, "_enabled", None)


# ── what gets which colour ─────────────────────────────────────────────

def test_a_failed_upload_is_red():
    painted = term.line("[Rumble] UPLOAD FAILED: Chrome is not reachable")
    assert term.BRIGHT_RED in painted
    assert painted.endswith(term.RESET)


def test_a_skip_is_yellow_not_red():
    """A circuit breaker is working as designed. It must not read as a
    crash, or the genuinely broken lines stop standing out."""
    painted = term.line("[Clips] facebook: skipped - circuit breaker open")
    assert term.YELLOW in painted
    assert term.BRIGHT_RED not in painted


def test_a_finished_upload_is_green():
    assert term.BRIGHT_GREEN in term.line(
        "[YouTube] uploaded successfully -> https://youtu.be/x")


def test_timing_lines_are_dimmed_out_of_the_way():
    assert term.DIM in term.line("[Timing] clip.mp4: render [copy] took 0.4s")


def test_a_failure_that_will_be_retried_reads_as_a_failure():
    """Both markers are present. The failure is the part worth seeing -
    'retrying in 900s' on its own looks like progress."""
    painted = term.line("attempt 1 FAILED: timeout; retrying in 60s")
    assert term.BRIGHT_RED in painted


def test_an_ordinary_line_is_left_alone():
    assert term.line("Processing: stream.ts") == "Processing: stream.ts"


# ── degrading safely ───────────────────────────────────────────────────

def test_nothing_is_painted_when_colour_is_off(monkeypatch):
    monkeypatch.setattr(term, "_enabled", False)
    text = "[Rumble] UPLOAD FAILED: everything"
    assert term.line(text) == text
    assert term.failure(text) == text


def test_a_redirected_stream_gets_no_colour():
    """A .log full of escape codes is worse than one with none."""
    assert term._detect(io.StringIO()) is False


def test_no_color_is_honoured(monkeypatch):
    """The de-facto standard: https://no-color.org"""
    monkeypatch.setenv("NO_COLOR", "1")
    assert term._detect(_FakeTTY()) is False


def test_force_color_overrides_a_lying_terminal(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert term._detect(io.StringIO()) is True


def test_a_dumb_terminal_gets_no_colour(monkeypatch):
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert term._detect(_FakeTTY()) is False


class _FakeTTY(io.StringIO):
    def isatty(self):
        return True


# ── the stdout wrapper ─────────────────────────────────────────────────

def test_every_line_written_comes_back_whole():
    """The text must survive colouring exactly. A dropped or duplicated
    character here would corrupt every line the program prints."""
    sink = io.StringIO()
    stream = term._ColouringStream(sink)
    stream.write("[Timing] one\n[Rumble] UPLOAD FAILED: two\nplain three\n")

    written = sink.getvalue()
    for code in (term.RESET, term.DIM, term.BOLD, term.BRIGHT_RED):
        written = written.replace(code, "")
    assert written == "[Timing] one\n[Rumble] UPLOAD FAILED: two\nplain three\n"


def test_a_progress_line_is_not_swallowed():
    """"Uploading... 10%" is written with no newline and redrawn in
    place. Holding it back until something ends the line would freeze
    the percentage on screen for the whole upload."""
    sink = io.StringIO()
    term._ColouringStream(sink).write("[YouTube] Uploading... 10%")
    assert sink.getvalue() == "[YouTube] Uploading... 10%"


def test_a_line_split_across_writes_is_coloured_once_it_ends():
    sink = io.StringIO()
    stream = term._ColouringStream(sink)
    stream.write("[Rumble] UPLOAD ")
    stream.write("FAILED: split\n")
    assert "FAILED: split" in sink.getvalue()


def test_write_reports_what_it_was_given():
    """print() and anything else writing here can rely on the count."""
    stream = term._ColouringStream(io.StringIO())
    assert stream.write("[Timing] x\n") == len("[Timing] x\n")


def test_wrapping_twice_is_harmless(monkeypatch):
    """A re-entrant path calling this again must not nest wrappers -
    each layer would colour the previous layer's escape codes."""
    monkeypatch.setattr(sys, "stdout", _FakeTTY())
    monkeypatch.setattr(term, "_enabled", True)
    assert term.colourise_stdout() is True
    once = sys.stdout
    assert term.colourise_stdout() is True
    assert sys.stdout is once


def test_colour_off_leaves_stdout_completely_alone(monkeypatch):
    monkeypatch.setattr(term, "_enabled", False)
    monkeypatch.setattr(term, "enable", lambda *a, **k: False)
    original = sys.stdout
    assert term.colourise_stdout() is False
    assert sys.stdout is original


def test_a_partial_receipt_is_not_green():
    """"1 of 2 landed, 1 failed" matches a success marker and is not a
    success. Green on a line that says something failed actively tells
    you not to look at it."""
    painted = term.line("[Report] Posted the receipt: 1 of 2 landed, 1 failed.")
    assert term.YELLOW in painted
    assert term.BRIGHT_GREEN not in painted


def test_attaching_to_chrome_reads_as_the_good_path():
    """The line whose ABSENCE meant Rumble had silently fallen back to
    the password form for weeks."""
    assert term.BRIGHT_GREEN in term.line(
        "[Rumble] Attached to Chrome at http://localhost:9222.")


def test_the_recorders_403_wall_is_yellow():
    """A wall of 403s in plain white looks like ordinary download
    output. It is the sign a manifest went stale and the recording is
    about to restart."""
    assert term.YELLOW in term.line(
        "[download] Got error: HTTP Error 403: Forbidden. "
        "Retrying fragment 108 (1/inf)...")


def test_eleven_hours_of_waiting_is_dimmed():
    """The recorder prints this every half hour all night."""
    assert term.DIM in term.line("[16:08:48] Still watching (10.5h).")

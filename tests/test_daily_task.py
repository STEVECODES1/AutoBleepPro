"""The daily clip task has to survive being unattended.

Two bugs lived in the handover between INSTALL-DAILY.bat, which writes the
Windows scheduled task, and CLIP-VODS.bat, which the task runs. Neither one
shows up when you double-click the file yourself, which is why both sat
there after the install was watched succeeding.

**One space.** The task command was written

    cmd /c set AUTOBLEEP_UNATTENDED=1 && "...\\CLIP-VODS.bat" 3 "..."

and cmd takes everything between the `=` and the `&&` as the value. The
variable holds ``"1 "``. Every ``if "%AUTOBLEEP_UNATTENDED%"=="1"`` in
CLIP-VODS.bat is therefore false, so:

  * ``--tidy-vods`` was never passed, and three VODs a day at 3-5 GB fill
    the drive inside a week;
  * the ``pause`` at the bottom ran - inside a scheduled task, with no
    keyboard attached. The task sits on it forever. Windows' default is to
    skip a new instance while one is still running, so the daily job runs
    exactly once, ever, and stays green in Task Scheduler while doing so.

**A file rewritten underneath itself.** CLIP-VODS.bat runs ``git pull``
partway down. cmd.exe does not read a batch file into memory; it reads a
line, runs it, and seeks back to a saved byte offset for the next. Pull a
change to CLIP-VODS.bat itself and that offset lands mid-line, so the rest
of the run executes fragments. It fails differently every time.

These are text assertions on batch files, which is unglamorous, but a
scheduled task that never runs twice cannot be caught any other way from
here.
"""

from __future__ import annotations

import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INSTALL = os.path.join(_REPO, "INSTALL-DAILY.bat")
CLIPVODS = os.path.join(_REPO, "CLIP-VODS.bat")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _body(text: str) -> str:
    """Everything that is not a REM comment.

    The comments explain both bugs at length and quote the broken forms, so
    a naive substring search finds them in the prose and passes.
    """
    keep = [ln for ln in text.splitlines()
            if not ln.strip().lower().startswith("rem")]
    return "\n".join(keep)


def _scheduled_command(text: str) -> str:
    """The command line the task will actually run.

    Inside schtasks' /tr argument the inner quotes are written \\" so they
    survive the outer pair; what Windows stores and later runs has them as
    plain quotes.
    """
    lines = [ln for ln in text.splitlines() if "/tr " in ln]
    assert len(lines) == 1, "the scheduled command moved"
    return lines[0].replace('\\"', '"')


def _cmd_set_value(command: str, name: str) -> str | None:
    """What cmd.exe would store, given `set NAME=...` inside a command line.

    Unquoted, the value runs to the next command separator and keeps any
    trailing space. Quoted - ``set "NAME=1"`` - the quotes bound it and the
    space falls outside.
    """
    quoted = re.search(r'set\s+"' + name + r'=([^"]*)"', command)
    if quoted:
        return quoted.group(1)
    bare = re.search(r"set\s+" + name + r"=(.*?)(?:&&|&|$)", command)
    if bare:
        return bare.group(1)
    return None


# ── the flag survives the trip into the scheduled task ───────────────────

def test_the_unattended_flag_is_not_stored_with_a_trailing_space():
    value = _cmd_set_value(_scheduled_command(_read(INSTALL)),
                           "AUTOBLEEP_UNATTENDED")

    assert value == "1", f"the task would store {value!r}, not '1'"


def test_the_broken_form_really_would_have_stored_a_space():
    """Guarding the guard - if this stops being true the test above is
    checking nothing."""
    broken = 'cmd /c set AUTOBLEEP_UNATTENDED=1 && "C:\\x\\CLIP-VODS.bat" 3 ""'

    assert _cmd_set_value(broken, "AUTOBLEEP_UNATTENDED") == "1 "


def test_clip_vods_compares_with_the_spaces_taken_out():
    """So a task installed by the older INSTALL-DAILY.bat starts behaving
    as soon as it pulls, without anyone re-running the installer."""
    body = _body(_read(CLIPVODS))

    assert 'set "UNATTENDED=%UNATTENDED: =%"' in body


def test_nothing_still_compares_the_raw_variable():
    body = _body(_read(CLIPVODS))

    assert '"%AUTOBLEEP_UNATTENDED%"=="1"' not in body


def test_the_pause_is_skipped_when_unattended():
    """A scheduled task has no keyboard. This is the line that hung it."""
    body = _body(_read(CLIPVODS))

    pauses = [ln for ln in body.splitlines() if ln.strip().endswith("pause")]
    assert pauses, "the pause vanished - then this test guards nothing"
    for line in pauses:
        assert '%UNATTENDED%' in line, f"unguarded pause: {line!r}"


def test_the_vods_are_tidied_away_when_unattended():
    body = _body(_read(CLIPVODS))

    assert 'if "%UNATTENDED%"=="1" set EXTRA=--tidy-vods' in body


# ── the run does not read a file git is rewriting ────────────────────────

def test_the_pull_happens_after_the_handover_to_a_copy():
    body = _body(_read(CLIPVODS))

    handover = body.index("AUTOBLEEP_STAGE2")
    pull = body.index("git pull")

    assert handover < pull, "git pull rewrites this file before it is copied"


def test_the_copy_is_handed_the_run_and_the_original_stops():
    """`exit /b` has to be on the same line as the call. cmd parses a whole
    line up front, so it runs from the parse buffer - it never seeks back
    into the file the pull just rewrote."""
    body = _body(_read(CLIPVODS))
    line = [ln for ln in body.splitlines()
            if "call " in ln and "autobleep_clipvods.bat" in ln]

    assert len(line) == 1
    assert "exit /b" in line[0], "the original would read on past the call"
    assert line[0].rstrip().endswith(")"), (
        "the call and exit must sit inside parentheses - `if cond a & b` "
        "runs b whatever the condition")


def test_the_copy_still_knows_where_the_repo_is():
    """%~dp0 inside the copy is TEMP. Every path has to come from
    AUTOBLEEP_ROOT instead, or the run looks for main.py in TEMP."""
    body = _body(_read(CLIPVODS))

    assert 'python "%AUTOBLEEP_ROOT%auto_uploader\\main.py"' in body
    for line in body.splitlines():
        if "python " in line or "cd /d" in line:
            continue
        assert "%~dp0auto_uploader" not in line, line


def test_a_failed_copy_still_runs_the_job():
    """A missing safety net is not a reason to skip the day's clips."""
    body = _body(_read(CLIPVODS))
    line = [ln for ln in body.splitlines()
            if "call " in ln and "autobleep_clipvods.bat" in ln][0]

    assert "if exist" in line, "a failed copy would hand over to nothing"
    assert 'if "%AUTOBLEEP_ROOT%"=="" set "AUTOBLEEP_ROOT=%~dp0"' in body


def test_the_copy_does_not_copy_itself_again():
    body = _body(_read(CLIPVODS))
    guards = [ln for ln in body.splitlines() if "AUTOBLEEP_STAGE2" in ln]

    assert len(guards) >= 3
    for line in guards:
        assert 'if not "%AUTOBLEEP_STAGE2%"=="1"' in line or 'set "AUTOBLEEP_STAGE2=1"' in line


def test_the_arguments_reach_the_copy():
    """The task passes the take-count and the source folder. Losing them
    means clipping the Rumble channel instead of D:\\videos stizz."""
    body = _body(_read(CLIPVODS))
    line = [ln for ln in body.splitlines()
            if "call " in ln and "autobleep_clipvods.bat" in ln][0]

    assert "%*" in line

"""
A half-merged checkout must say so once, not fail forever.

On 2026-09-21 a `git stash pop` conflicted and wrote its markers into
auto_uploader/utils/config.py. The uploader then printed

    File "auto_uploader/utils/config.py", line 324
        <<<<<<< Updated upstream
    SyntaxError: invalid syntax

twice, and the keepalive restarted it, and it printed the same thing
again, five times in seventy-five seconds. Nothing in that output says
the checkout is half-merged or what to type, and no restart could ever
have fixed it: nothing about a conflicted file changes by running it
again.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.checkout_sanity import (  # noqa: E402
    BROKEN_CHECKOUT,
    check,
    conflicted_lines,
    find_conflicts,
    report,
)

CONFLICTED = '''\
def load(rb):
    return Config(
<<<<<<< Updated upstream
        cdp_url=_rumble_cdp_url(rb),
=======
        cdp_url=rb.get("cdp_url") or None,
>>>>>>> Stashed changes
    )
'''


def test_all_three_markers_are_found():
    assert [n for n, _ in conflicted_lines(CONFLICTED)] == [3, 5, 7]


def test_a_comment_underline_is_not_a_conflict():
    """This project's own source is full of them, including the module
    being tested. A false positive here would refuse to start a
    perfectly good checkout."""
    assert conflicted_lines("# " + "=" * 60) == []
    assert conflicted_lines("# ── Section ───────────────") == []
    assert conflicted_lines("assert a ======= b") == [], \
        "only a line that IS the marker counts"


def test_the_markers_inside_this_project_do_not_trip_it():
    """checkout_sanity.py and this file both contain the marker strings
    as data. Scanning the real tree must still come back clean."""
    assert find_conflicts(_REPO) == []
    assert check(_REPO) == 0


def test_a_conflicted_tree_is_reported_not_fixed(tmp_path):
    """Resolving a conflict means choosing a side, and guessing that on
    somebody's behalf is how work gets silently thrown away."""
    package = tmp_path / "auto_uploader" / "utils"
    package.mkdir(parents=True)
    broken = package / "config.py"
    broken.write_text(CONFLICTED)

    problems = find_conflicts(str(tmp_path))
    assert problems, "the conflict was not noticed"
    assert all(str(broken) == p for p, _, _ in problems)

    # Untouched.
    assert broken.read_text() == CONFLICTED


def test_the_message_says_what_to_type(capsys, tmp_path):
    package = tmp_path / "autoreel"
    package.mkdir(parents=True)
    (package / "clip_maker.py").write_text(CONFLICTED)

    assert check(str(tmp_path)) == BROKEN_CHECKOUT

    printed = capsys.readouterr().out
    assert "half-merged" in printed.lower()
    assert "git reset --hard HEAD" in printed
    # ...and that the fix is safe, because it does not look safe.
    assert "git stash list" in printed
    assert "clip_maker.py" in printed


def test_the_exit_code_is_distinct_from_an_ordinary_crash():
    """The keepalive tells them apart: 1 is "something went wrong" and
    is worth retrying, 3 is "this cannot work until a human fixes it"
    and is not."""
    assert BROKEN_CHECKOUT == 3
    assert BROKEN_CHECKOUT != 1


def test_the_uploader_checks_before_it_imports_anything():
    """The failure happened AT an import, so a check placed after the
    imports never gets to run."""
    body = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                encoding="utf-8").read()

    # At the start of a line: the guard's own comment quotes this
    # import, and matching that instead would pass no matter where the
    # guard actually sat.
    guard = body.index("\n_broken = _check_checkout()")
    first_real_import = body.index("\nfrom utils.config import")
    assert guard < first_real_import, \
        "the guard is below the import it is supposed to survive"


def test_the_keepalive_stops_instead_of_looping():
    body = open(os.path.join(_REPO, "_RUN_UPLOADER.bat"),
                encoding="utf-8", errors="replace").read()

    assert '"%CODE%"=="3" goto broken' in body
    assert ":broken" in body
    # The check has to come before the restart counter, or it restarts
    # once anyway before noticing.
    assert body.index('goto broken') < body.index("set /a RESTARTS+=1")


# ── the launcher must never stop and wait for a keystroke ─────────────

def test_the_pull_cannot_open_an_editor():
    """A plain `git pull` that has to MERGE opens an editor for the
    merge message. On Windows that is vim, in a batch window, with no
    sign of what happened or that Esc :wq is the way out - the pull
    simply appears to hang, which is exactly what it did on
    2026-09-21.
    """
    body = open(os.path.join(_REPO, "START.bat"),
                encoding="utf-8", errors="replace").read()

    assert "git pull --no-edit" in body
    assert "\ngit pull\n" not in body, \
        "a bare pull opens vim the moment it has to merge"
    # ...and anything else git might open an editor for.
    assert 'set "GIT_EDITOR=true"' in body
    assert body.index("GIT_EDITOR") < body.index("git pull --no-edit")


def test_local_edits_are_parked_rather_than_blocking_the_pull():
    """Edits in the working tree abort a pull outright, and the old
    launcher then said "continuing with the version already on disk"
    and started anyway - which is how a whole night ran on a two-day-old
    checkout."""
    body = open(os.path.join(_REPO, "START.bat"),
                encoding="utf-8", errors="replace").read()

    assert "git stash push -u" in body
    assert body.index("git stash push -u") < body.index("git pull --no-edit")
    # Parked, not discarded.
    assert "git stash pop" in body, "say how to get them back"

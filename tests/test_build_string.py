"""The version line has to be true.

It exists for one job: proving which code is actually running. Hand-bumped
it failed at that job - a run printed the previous day's build, a correct
`git pull` was read as a failed one, and two rounds went into re-pulling a
checkout that was already current.

A constant somebody has to remember to edit reports the last time it was
EDITED. Read from the checkout, it reports the code.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
if _UPLOADER not in sys.path:
    sys.path.insert(0, _UPLOADER)


def _main():
    spec = importlib.util.spec_from_file_location(
        "_main_build", os.path.join(_UPLOADER, "main.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _head():
    out = subprocess.run(["git", "log", "-1", "--format=%h"],
                         cwd=_REPO, capture_output=True, text=True)
    return out.stdout.strip()


def test_the_build_names_the_commit_that_is_running():
    head = _head()
    if not head:
        return  # not a checkout; the fallback test below covers that

    assert head in _main()._build_string()


def test_it_is_not_a_constant_anybody_has_to_remember_to_edit():
    """The whole failure was a string that only changed when someone
    thought to change it."""
    source = open(os.path.join(_UPLOADER, "main.py"), encoding="utf-8").read()
    head, _, _ = source.partition("def _build_string")
    assert "BUILD = _build_string()" in source
    assert 'BUILD = "2026' not in head, "back to a hand-bumped constant"


def test_a_copy_with_no_git_says_so_rather_than_guessing(monkeypatch):
    """An extract or a zip has no commit to name. Obviously vague beats
    confidently stale - the stale version is what caused the confusion."""
    main = _main()

    def no_git(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    assert main._build_string() == main.BUILD_FALLBACK


def test_a_failed_git_call_falls_back_too(monkeypatch):
    main = _main()

    class Failed:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Failed())
    assert main._build_string() == main.BUILD_FALLBACK


def test_uncommitted_edits_are_admitted(monkeypatch):
    """A checkout with edits is not the commit it names, and that
    difference is exactly what this line gets asked about."""
    main = _main()

    class Answer:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    answers = [Answer("2026-08-17 abc1234"),
               Answer(" M auto_uploader/main.py\n")]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: answers.pop(0))

    line = main._build_string()

    assert "changed" in line
    assert "main.py" in line, (
        "naming the file is the point - '(+ local edits)' says something "
        "differs and leaves you to find out what, every run")



def test_a_clean_checkout_is_not_marked_dirty(monkeypatch):
    main = _main()

    class Answer:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    answers = [Answer("2026-08-17 abc1234 a commit subject"), Answer("")]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: answers.pop(0))

    line = main._build_string()

    assert line.startswith("2026-08-17 abc1234")
    assert "edited" not in line


def test_untracked_files_are_not_local_edits(monkeypatch):
    """config.json, cookies.txt and logs/ are untracked by design. Counting
    them marks every real installation as edited forever, and a warning
    that is always on is the same as no warning."""
    main = _main()
    asked = []

    class Answer:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    def fake(cmd, **_k):
        asked.append(cmd)
        return Answer("2026-08-17 abc1234 subject" if "log" in cmd else "")

    monkeypatch.setattr(subprocess, "run", fake)
    main._build_string()

    status = next(c for c in asked if "status" in c)
    assert "--untracked-files=no" in status


def test_deleted_files_are_named_as_gone_not_edited(monkeypatch):
    """Five different .gitkeep files came out as

        (edited: .gitkeep, .gitkeep, .gitkeep, +2 more)

    which is worse than saying nothing: one name for five files, and no
    clue what is wrong. The folder tells them apart, and the status letter
    says whether they were changed or deleted - the difference between
    "you edited something" and "a folder got emptied"."""
    main = _main()

    class Answer:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    answers = [Answer("2026-08-17 abc1234"),
               Answer(" D auto_uploader/watch_folder/.gitkeep\n"
                      " D auto_uploader/logs/.gitkeep\n")]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: answers.pop(0))

    line = main._build_string()

    assert "gone:" in line
    assert "watch_folder/.gitkeep" in line
    assert "logs/.gitkeep" in line
    assert line.count(".gitkeep") == 2, "the two files must be told apart"


def test_changed_and_deleted_are_reported_separately(monkeypatch):
    main = _main()

    class Answer:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    answers = [Answer("2026-08-17 abc1234"),
               Answer(" M auto_uploader/main.py\n"
                      " D auto_uploader/logs/.gitkeep\n")]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: answers.pop(0))

    line = main._build_string()

    assert "changed: auto_uploader/main.py" in line
    assert "gone: logs/.gitkeep" in line


def test_a_rename_reports_where_the_file_landed(monkeypatch):
    """git prints `old -> new` for a rename. Reporting the old path names
    something that is no longer there."""
    main = _main()

    class Answer:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    answers = [Answer("2026-08-17 abc1234"),
               Answer('R  auto_uploader/old.py -> auto_uploader/new.py\n')]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: answers.pop(0))

    line = main._build_string()

    assert "new.py" in line
    assert "old.py" not in line


def test_the_python_version_is_always_there(monkeypatch):
    """It was the invisible fact behind a whole day of failures."""
    import platform

    main = _main()

    class Answer:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    answers = [Answer("2026-08-17 abc1234"), Answer("")]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: answers.pop(0))

    assert f"Python {platform.python_version()}" in main._build_string()


def test_the_commit_subject_is_not_in_the_banner():
    """The subjects here are sentences, so including %s produced

        Build: 2026-08-22 75bd715 Python 3.13 deleted the module the
        censor runs on (+ local edits)

    a version line that reads like a crash report and names a Python
    version that is not the one running."""
    import os

    source = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "auto_uploader", "main.py"), encoding="utf-8").read()
    spot = source.index('"git", "log", "-1"')

    assert "%cs %h" in source[spot:spot + 120]
    assert "%s" not in source[spot:spot + 120]

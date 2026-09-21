"""
Is the code on disk actually code?

WHY THIS EXISTS
---------------
A `git stash pop` that conflicts does not fail loudly. It writes the
conflict INTO the files and leaves them there:

    <<<<<<< Updated upstream
        cdp_url=_rumble_cdp_url(rb),
    =======
        cdp_url=rb.get("cdp_url") or None,
    >>>>>>> Stashed changes

That is not Python. The uploader then died on

    File "auto_uploader/utils/config.py", line 324
        <<<<<<< Updated upstream
    SyntaxError: invalid syntax

...and the keepalive restarted it, and it died again, five times and
counting, fifteen seconds apart, each one printing the same traceback
twice. Nothing in that output says "your checkout is half-merged" or
how to fix it, and a restart cannot fix it, because nothing about a
conflicted file changes by running it again.

WHY IT IS ITS OWN MODULE WITH NO IMPORTS
----------------------------------------
It has to run BEFORE the thing it is checking for can break. The
failure above happened at `from utils.config import ...`, which is an
ordinary import at the top of main.py - so a check that lives behind
any of those imports never gets to run. This imports nothing but the
standard library and is called first.

WHAT IT DOES NOT DO
-------------------
It does not fix anything. Resolving a conflict means choosing which
side to keep, and guessing that on somebody's behalf is how work gets
silently thrown away. It names the files and the command, and stops.
"""

from __future__ import annotations

import os

# Exit code for "the checkout is broken". Distinct so the keepalive can
# tell a permanent condition from a crash worth retrying - see
# _RUN_UPLOADER.bat. 1 is "something went wrong", which is retryable.
BROKEN_CHECKOUT = 3

# Git writes all three. Matched at the START of a line, which is where
# git puts them and where they would be a syntax error - the same
# characters inside a string or a regex are ordinary content, and this
# file is itself full of them.
_MARKERS = ("<<<<<<< ", "=======", ">>>>>>> ")

# Only the code that actually gets imported. Scanning the whole tree
# would mean walking clips and recordings - gigabytes - on every start.
_SCANNED = (
    ("auto_uploader",),
    ("auto_uploader", "utils"),
    ("auto_uploader", "publishers"),
    ("autoreel",),
    ("tools",),
)


def conflicted_lines(text: str) -> list:
    """[(line number, line)] for every conflict marker in `text`."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.rstrip()
        # "=======" alone on a line. A longer run of equals signs is a
        # comment underline, which this file has several of.
        if stripped == "=======":
            found.append((number, stripped))
        elif stripped.startswith(("<<<<<<< ", ">>>>>>> ")):
            found.append((number, stripped))
    return found


def _python_files(root: str) -> list:
    paths = []
    for parts in _SCANNED:
        folder = os.path.join(root, *parts)
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if name.endswith(".py"):
                paths.append(os.path.join(folder, name))
    return sorted(paths)


def find_conflicts(root: str) -> list:
    """[(path, line number, line)] across the source tree."""
    problems = []
    for path in _python_files(root):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        for number, line in conflicted_lines(text):
            problems.append((path, number, line))
    return problems


def report(problems: list) -> str:
    """What to print. Says the fix, not just the problem."""
    files = []
    for path, _, _ in problems:
        if path not in files:
            files.append(path)

    lines = [
        "",
        "=" * 60,
        " THIS CHECKOUT IS HALF-MERGED - nothing can run",
        "=" * 60,
        "",
        " A `git stash pop` or a merge hit a conflict and wrote the",
        " conflict markers into your files. They are not valid Python,",
        " so every restart will fail exactly the same way.",
        "",
    ]
    for path in files[:10]:
        numbers = [str(n) for p, n, _ in problems if p == path][:6]
        lines.append(f"   {path}   line(s) {', '.join(numbers)}")
    if len(files) > 10:
        lines.append(f"   ...and {len(files) - 10} more file(s)")
    lines += [
        "",
        " To throw the half-merge away and take the tested code:",
        "",
        "     git reset --hard HEAD",
        "",
        " Nothing is lost doing that. A conflicted `git stash pop` does",
        " NOT drop the stash, so your own edits are still there:",
        "",
        "     git stash list",
        "",
        "=" * 60,
        "",
    ]
    return "\n".join(lines)


def check(root: str = "") -> int:
    """0 when the tree is sane, BROKEN_CHECKOUT when it is not."""
    if not root:
        # .../auto_uploader/utils/checkout_sanity.py -> repository root
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
    problems = find_conflicts(root)
    if not problems:
        return 0
    print(report(problems))
    return BROKEN_CHECKOUT

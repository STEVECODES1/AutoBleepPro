"""Shared setup for the test suite.

config.json is deliberately NOT tracked in git - config.example.json is.
Settings are the operator's, and a `git pull` must never collide with a
switch they flipped locally. That is a real problem this project hit
three times in one night: every pull aborted with

    error: Your local changes to the following files would be
    overwritten by merge: auto_uploader/config.json

leaving the machine several builds behind while fixes sat unused.

The consequence for tests is that a fresh checkout has no config.json.
Rather than teach fourteen test files to look in two places, this makes
the file exist before anything is collected - which is also exactly what
main.py does on its first run.
"""

from __future__ import annotations

import os
import shutil

_UPLOADER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "auto_uploader")


def pytest_configure(config):
    live = os.path.join(_UPLOADER, "config.json")
    example = os.path.join(_UPLOADER, "config.example.json")
    if not os.path.isfile(live) and os.path.isfile(example):
        shutil.copyfile(example, live)

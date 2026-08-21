"""The daily clip run, pointed at a library of old videos.

All of this was built and never switched on. What was missing was a way
to tell the scheduled task to take videos from a local folder - 203 old
streams that still have funny moments in them - instead of the Rumble
channel.

The rule that makes that safe: a folder is only ever READ.
"""

from __future__ import annotations

import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(_REPO, name), encoding="utf-8",
              errors="replace") as handle:
        return handle.read()


def test_the_task_can_be_pointed_at_a_folder():
    body = _read("INSTALL-DAILY.bat")

    assert "set SOURCE=%~2" in body
    assert 'CLIP-VODS.bat\\" 3 \\"%SOURCE%\\"' in body


def test_no_folder_still_means_the_rumble_channel():
    """The default has to keep working with no arguments at all."""
    body = _read("CLIP-VODS.bat")

    assert 'if "%SOURCE%"=="" set SOURCE=https://rumble.com/user/stackswopo10k' in body


def test_the_library_folder_is_documented_with_its_real_name():
    body = _read("INSTALL-DAILY.bat")

    assert "videos stizz" in body


def test_the_tidy_up_cannot_eat_a_library():
    """--tidy-vods runs unattended. It must refuse any folder except this
    tool's own downloads, or a daily task would delete 203 old streams."""
    import sys

    uploader = os.path.join(_REPO, "auto_uploader")
    if uploader not in sys.path:
        sys.path.insert(0, uploader)
    body = open(os.path.join(uploader, "main.py"), encoding="utf-8").read()

    assert "def tidy_downloaded_vods" in body
    assert "that folder is yours, not" in body


def test_it_still_runs_unattended():
    """A scheduled task would sit on a keypress forever."""
    assert "AUTOBLEEP_UNATTENDED=1" in _read("INSTALL-DAILY.bat")

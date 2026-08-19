"""The clip's title, held at the top of the frame.

Taken from an account posting this channel's own clips and doing better
with them: every post carries a burned-in title line at the top, and it
was the only visible thing their clips had that ours did not. It answers
"why am I still watching this" in the first half second, which is the
whole fight on a feed that autoplays with the sound off.

Off by default. It changes how every clip looks, and that is the account
owner's call to make rather than to discover.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.captions import (HOOK_MARGIN_V, build_ass,  # noqa: E402
                               caption_file_for_clip)


def _segments():
    return [{"start": 0.0, "end": 4.0, "text": "hello there",
             "words": [{"word": "hello", "start": 0.5, "end": 1.0},
                       {"word": "there", "start": 1.0, "end": 1.6}]}]


def test_the_hook_is_pinned_to_the_top():
    """Alignment 8 is top-centre. The captions are at the bottom, and a
    title competing with the words lighting up helps nobody."""
    out = build_ass([], hook="Robbed with no firearm", hook_seconds=30.0)

    style = next(l for l in out.splitlines() if l.startswith("Style: Hook"))
    # Name, Fontname, Fontsize, Primary, Outline, Back, Bold, Italic,
    # BorderStyle, Outline, Shadow, Alignment - twelfth field.
    assert style.split(",")[11] == "8"
    assert style.endswith(f"{HOOK_MARGIN_V},1")  # clear of the phone's UI


def test_the_hook_lasts_the_whole_clip():
    out = build_ass([], hook="Robbed with no firearm", hook_seconds=52.0)

    line = next(l for l in out.splitlines() if ",Hook,," in l)
    assert line.startswith("Dialogue: 0,0:00:00.00,0:00:52.00,Hook")


def test_no_hook_means_no_hook_line():
    """Nothing changes for anyone who has not asked for this."""
    out = build_ass([], hook="", hook_seconds=30.0)

    assert ",Hook,," not in out


def test_a_blank_hook_is_not_a_hook():
    assert ",Hook,," not in build_ass([], hook="   ", hook_seconds=30.0)


def test_a_clip_with_no_speech_still_gets_its_hook(tmp_path):
    """Returning None on 'no phrases' threw the title away on exactly the
    clips that need one most - the ones where nothing is said."""
    path = tmp_path / "c.ass"

    written = caption_file_for_clip(str(path), [], 10.0, 40.0,
                                    hook="No firearm needed")

    assert written and os.path.isfile(str(path))
    body = path.read_text(encoding="utf-8")
    assert "NO FIREARM NEEDED" in body


def test_a_silent_clip_with_no_hook_is_still_nothing(tmp_path):
    assert caption_file_for_clip(str(tmp_path / "c.ass"), [], 0.0, 4.0) is None


def test_the_hook_covers_the_clip_it_was_written_for(tmp_path):
    """hook_seconds is the clip's length, not the source's."""
    path = tmp_path / "c.ass"
    caption_file_for_clip(str(path), _segments(), 0.0, 4.0,
                          hook="Robbed")

    line = next(l for l in path.read_text().splitlines() if ",Hook,," in l)
    assert "0:00:04.00" in line


def test_the_hook_is_escaped_like_any_other_text(tmp_path):
    """A model-written title can contain anything, and a stray brace is
    an ASS override block - which would eat the rest of the line."""
    out = build_ass([], hook="what {the} hell", hook_seconds=5.0)

    line = next(l for l in out.splitlines() if ",Hook,," in l)
    assert "{the}" not in line


def test_it_is_off_unless_asked_for():
    from autoreel.clip_maker import ClipMaker

    assert ClipMaker(output_dir="x").burn_hook is False

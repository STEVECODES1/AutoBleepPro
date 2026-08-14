"""
Every filter chain, actually run through ffmpeg.

These exist because a filter chain that is wrong is not wrong in a way
any amount of string-checking finds. The motion crop was built, reviewed,
unit-tested on its output string, and committed - and it could never have
rendered a single frame, because `min(iw,ih*9/16)` contains a comma and a
comma is a FILTER SEPARATOR to ffmpeg's parser. The graph failed to build
with "No such filter: 'ih*9/16)...'".

Nothing short of running it would have caught that. So these tests render
five real frames through each strategy and check the output is a playable
1080x1920 clip with its audio.
"""

import os
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.clip_maker import (CROP_WIDTH_EXPR, ClipSpec, have_ffmpeg,
                                 render_clip)
from autoreel.crop_strategy import resolve_region, resolve_stack

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """A short 16:9 clip with sound, standing in for a stream."""
    path = str(tmp_path_factory.mktemp("src") / "source.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        path], check=True)
    return path


def _describe(path: str) -> tuple:
    video = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_name", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    return video, audio


@pytest.mark.parametrize("strategy", ["center", "fit", "region"])
def test_the_simple_strategies_render(source, tmp_path, strategy):
    out = str(tmp_path / f"{strategy}.mp4")

    render_clip(source, ClipSpec(1.0, 4.0, 1), out, strategy, None,
                "libx264", "ultrafast", 30,
                region=resolve_region({"clips": {"profile": "monkey"}}))

    assert _describe(out) == ("1080,1920", "aac")


def test_the_stack_renders(source, tmp_path):
    """Two crops of one input, joined - a filter_complex, not a -vf."""
    out = str(tmp_path / "stack.mp4")

    render_clip(source, ClipSpec(1.0, 4.0, 1), out, "stack", None,
                "libx264", "ultrafast", 30,
                stack=resolve_stack({"clips": {"profile": "monkey_stack"}}))

    assert _describe(out) == ("1080,1920", "aac"), \
        "the stack lost its audio - filter_complex needs an explicit -map"


def test_the_motion_crop_renders(source, tmp_path):
    """THE regression. A comma inside min() is a filter separator, so an
    unquoted expression splits the chain mid-way and nothing builds."""
    from autoreel.motion_region import commands_file

    commands = tmp_path / "pan.cmds"
    commands.write_text(commands_file(
        [(0.0, 0.30), (1.0, 0.45), (2.0, 0.62)], CROP_WIDTH_EXPR))
    out = str(tmp_path / "motion.mp4")

    render_clip(source, ClipSpec(1.0, 4.0, 1), out, "center", None,
                "libx264", "ultrafast", 30,
                motion_commands=str(commands))

    assert _describe(out) == ("1080,1920", "aac")


def test_burned_captions_render_and_are_censored(source, tmp_path):
    """The words reach the screen, and the flagged ones do not."""
    from autoreel.captions import STYLE_WORD, caption_file_for_clip

    spoken = "bro he actually said that shit on stream".split()
    words = [{"word": w, "start": 1.0 + i * 0.3, "end": 1.0 + i * 0.3 + 0.25}
             for i, w in enumerate(spoken)]
    ass = caption_file_for_clip(
        str(tmp_path / "c.ass"), [{"start": 1.0, "end": 4.0, "words": words}],
        1.0, 4.0, style=STYLE_WORD, uppercase=True)
    assert ass, "no caption file was written"

    body = open(ass, encoding="utf-8").read().upper()
    assert "SHIT" not in body and "S***" in body

    out = str(tmp_path / "captioned.mp4")
    render_clip(source, ClipSpec(1.0, 4.0, 1), out, "center", ass,
                "libx264", "ultrafast", 30)

    assert _describe(out) == ("1080,1920", "aac")


def test_a_stack_with_captions_renders(source, tmp_path):
    """Captions go on after the join. If they were applied per half the
    graph would need two subtitle filters and the text would double."""
    from autoreel.captions import STYLE_WORD, caption_file_for_clip

    words = [{"word": "yo", "start": 1.0, "end": 1.5}]
    ass = caption_file_for_clip(
        str(tmp_path / "c2.ass"), [{"start": 1.0, "end": 4.0, "words": words}],
        1.0, 4.0, style=STYLE_WORD, uppercase=True)
    out = str(tmp_path / "stackcap.mp4")

    render_clip(source, ClipSpec(1.0, 4.0, 1), out, "stack", ass,
                "libx264", "ultrafast", 30,
                stack=resolve_stack({"clips": {"profile": "monkey_stack"}}))

    assert _describe(out) == ("1080,1920", "aac")


def test_a_quote_in_the_filename_does_not_break_the_caption_path(source, tmp_path):
    """THE regression. `subtitles='...'` is a QUOTED filter argument, and
    inside those quotes a backslash is not an escape - so an escaped \\'
    ends the quote early and the rest of the path is read as filter
    options. A real stream called

        Stackswopo 'copyrighting all yall plug channels' 08.13.26

    produced a caption path ffmpeg could not open, and all twenty clips
    in that run failed on it. These files are internal, so the fix is to
    never put such a character in one.
    """
    from autoreel.captions import STYLE_WORD, caption_file_for_clip
    from autoreel.clip_maker import safe_stem

    basename = "Stackswopo 'copyrighting all yall plug channels' 08.13.26"
    assert "'" not in safe_stem(basename)

    words = [{"word": w, "start": 1.0 + i * 0.3, "end": 1.0 + i * 0.3 + 0.25}
             for i, w in enumerate("bro he said that".split())]
    ass = caption_file_for_clip(
        str(tmp_path / f".{safe_stem(basename)}_clip01.ass"),
        [{"start": 1.0, "end": 4.0, "words": words}], 1.0, 4.0,
        style=STYLE_WORD, uppercase=True)

    out = str(tmp_path / "quoted.mp4")
    render_clip(source, ClipSpec(1.0, 4.0, 1), out, "center", ass,
                "libx264", "ultrafast", 32, watermark=False)

    assert _describe(out) == ("1080,1920", "aac")


def test_a_quote_in_the_OUTPUT_path_is_fine(source, tmp_path):
    """The output is a plain argument, not a filter argument, so it needs
    no sanitising - and stripping quotes from the clip NAME would lose
    part of the stream's real title for no reason."""
    out = str(tmp_path / "Stackswopo 'Plug Channels' - Clip 01.mp4")

    render_clip(source, ClipSpec(1.0, 3.0, 1), out, "center", None,
                "libx264", "ultrafast", 32, watermark=False)

    assert os.path.exists(out)


def test_the_safe_stem_keeps_something_readable():
    """It ends up in a folder listing beside the clips; a hash would be
    safe and useless."""
    from autoreel.clip_maker import safe_stem

    stem = safe_stem("Stackswopo 'copyrighting all yall' 08.13.26")

    assert "Stackswopo" in stem
    assert len(stem) <= 60
    assert safe_stem("") and safe_stem("''''")


def test_a_dotted_date_is_stripped_from_clip_names():
    """08.13.26 lost its dots to the filesystem-safe pass and came out as
    "081326" in the middle of every clip name."""
    from autoreel.clip_maker import ClipSpec as Spec
    from autoreel.clip_maker import clip_filename

    name = clip_filename("Stackswopo 'Plug Channels' 08.13.26 Full Live Stream",
                         Spec(0, 10, 1))

    assert "081326" not in name and "08.13.26" not in name
    assert name == "Stackswopo 'Plug Channels' Full Live Stream - Clip 01.mp4"

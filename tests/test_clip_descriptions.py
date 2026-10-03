"""A Rumble clip read 'This stream first aired on 10/2/26 with the title
"You're the roblox guy, ain't no way"' and 'This is a replay of one of my
own past streams' - the clip's own topic and its post date, filled into
the full-stream template. And a stream went up as '"7 DAYS ON YT"" ...'."""

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.templating import build_clip_description, build_title  # noqa: E402

RUMBLE = ("My YouTube Channel: https://www.youtube.com/@StackswopoGames\n"
          "Stack's Twitch Channel: https://www.twitch.tv/stackswopo\n\n"
          "This stream first aired on [DATE] with the title \"[STREAM TITLE]\" "
          "on the Stackswopo channel.\n\n"
          "This is a replay of one of my own past streams, archived here so it "
          "stays available.\n\n"
          "Tags: #StacksWopo #StreamReplay #FullVOD #FunnyMoments #GTA5 "
          "#UncutReplay")


def test_a_clip_names_the_stream_it_came_from():
    text = build_clip_description(RUMBLE, "7 DAYS ON YT", "9/30/26")
    assert text.startswith('Clip from the "7 DAYS ON YT" stream (9/30/26)')
    assert "first aired" not in text and "replay" not in text.lower()
    assert "https://www.twitch.tv/stackswopo" in text
    assert "#FunnyMoments" in text and "#FullVOD" not in text


def test_no_source_note_still_reads_as_a_clip():
    text = build_clip_description(RUMBLE, "", "")
    assert text.startswith("Clip from a Stackswopo stream.")
    assert "first aired" not in text


def test_a_stray_quote_in_the_name_is_not_doubled():
    fmt = '"{title}" {date} Stackswopo Stream'
    assert build_title('7 DAYS ON YT"', "10/1/26", fmt) == \
        '"7 DAYS ON YT" 10/1/26 Stackswopo Stream'


def test_delivered_clips_carry_their_stream(tmp_path):
    import main

    clip = tmp_path / "out" / "c.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"x")
    run = SimpleNamespace(source_path=str(tmp_path / "'7 DAYS ON YT' 9-30-26 Stackswopo Stream.ts"),
                          source_title="7 DAYS ON YT")
    cfg = SimpleNamespace(general=SimpleNamespace(date_style="M/D/YY"))
    main._write_clip_source(str(clip), run, cfg)
    origin = main._clip_source(str(clip))
    assert origin["stream_title"] == "7 DAYS ON YT"
    assert origin["stream_date"] == "9/30/26"

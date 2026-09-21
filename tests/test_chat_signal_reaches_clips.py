"""The audience's own verdict, never once read.

The scorer has always been able to weight a window by CHAT - messages per
second, counted and the log deleted, exactly as asked. The code's own
note on it:

    chat is an opinion and volume is a measurement: a hundred people
    typing at once is a much better reason to clip something than a loud
    noise is.

And it never ran. The chat step needs a URL; a recording is a local file;
so every clip this project has made was picked from transcript shape and
loudness while the strongest available signal sat unused. "The clips
aren't funny" was the symptom.

The recorder knows the URL. It just never wrote it down.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "tools"),
              os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from record_stream import remember_source, source_sidecar  # noqa: E402
from utils.clip_runner import source_beside  # noqa: E402

LIVE = "https://www.youtube.com/@stackswopo_/live"


def test_a_recording_notes_where_it_came_from(tmp_path):
    video = tmp_path / "Stackswopo youtube live 2026-08-21.mp4"
    video.write_bytes(b"x")

    written = remember_source(str(video), LIVE)

    assert written == source_sidecar(str(video))
    assert source_beside(str(video)) == LIVE


def test_the_clipper_finds_it(tmp_path):
    """The whole point - this is what switches the chat step on."""
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x")
    remember_source(str(video), LIVE)

    assert source_beside(str(video)) == LIVE


def test_a_censored_copy_is_still_the_same_stream(tmp_path):
    """Clips are cut from <name>_CENSORED_silence.mp4, and that is the
    same stream with the same chat."""
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x")
    remember_source(str(video), LIVE)
    censored = tmp_path / "stream_CENSORED_silence.mp4"
    censored.write_bytes(b"x")

    assert source_beside(str(censored)) == LIVE


def test_a_video_with_no_note_reads_as_no_url(tmp_path):
    """A file somebody dropped in by hand - the other signals still
    work, exactly as before."""
    video = tmp_path / "downloaded.mp4"
    video.write_bytes(b"x")

    assert source_beside(str(video)) == ""


def test_something_that_is_not_a_url_is_refused(tmp_path):
    """A stray .source.txt must not be handed to a downloader."""
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x")
    (tmp_path / "stream.source.txt").write_text("Stackswopo Stream")

    assert source_beside(str(video)) == ""


def test_no_url_writes_nothing(tmp_path):
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x")

    assert remember_source(str(video), "") == ""
    assert not os.path.exists(source_sidecar(str(video)))


def test_an_unwritable_place_is_not_a_crash():
    """A recording that cannot leave a note is still a recording."""
    assert remember_source("/no/such/folder/stream.ts", LIVE) == ""


def test_the_clipper_asks_for_it_when_none_was_given():
    """It has to be wired in, not merely available."""
    body = open(os.path.join(_REPO, "auto_uploader", "utils",
                             "clip_runner.py"), encoding="utf-8").read()

    assert "source_url = source_beside(source_path)" in body
    # ...and still only reads chat when it is turned on.
    assert 'clips_cfg.get("use_chat", True)' in body


def test_the_recorder_writes_it_on_delivery():
    body = open(os.path.join(_REPO, "tools", "record_stream.py"),
                encoding="utf-8").read()

    assert "remember_source(destination," in body
    # ...and writes the RESOLVED address, not the channel /live one.
    # self.url is youtube.com/@stackswopo_/live, which points at
    # whatever is live right now and at nothing once the stream ends -
    # so the sidecar written from it was always dead by the time the
    # clip picker read it back. See resolved_watch_url.
    assert "resolved_watch_url(self.url, self.video_id)" in body
    assert "remember_source(destination, self.url)" not in body, \
        "this writes an address that expires when the stream does"


# ── which file to count ───────────────────────────────────────────────
#
# Found by actually running it against a live stream rather than by
# reading the code: yt-dlp does not leave one file.

def test_a_single_fragment_is_never_counted_as_the_whole_chat(tmp_path):
    """A real fetch of a live stream leaves all three of these:

        chat.live_chat.json             191057   the log
        chat.live_chat.json.part        191057   the same bytes, in progress
        chat.live_chat.json.part-Frag10  17219   ONE fragment

    All three contain "live_chat". Taking whichever os.listdir returned
    first was a one-in-three chance of counting a tenth of the chat -
    which still parses, still produces a curve, and reads as "the
    audience was quiet all stream except one stretch". Nothing
    downstream could tell.
    """
    from autoreel.chat_energy import pick_chat_log

    (tmp_path / "chat.live_chat.json").write_text("x" * 191057)
    (tmp_path / "chat.live_chat.json.part").write_text("x" * 191057)
    (tmp_path / "chat.live_chat.json.part-Frag10").write_text("x" * 17219)

    assert pick_chat_log(str(tmp_path)).endswith("chat.live_chat.json")


def test_a_part_file_is_used_when_it_is_all_there_is(tmp_path):
    """An interrupted fetch has no finished .json, and most of the chat
    beats none of it."""
    from autoreel.chat_energy import pick_chat_log

    (tmp_path / "chat.live_chat.json.part").write_text("x" * 5000)
    (tmp_path / "chat.live_chat.json.part-Frag3").write_text("x" * 100)

    assert pick_chat_log(str(tmp_path)).endswith(".part")


def test_empty_and_missing_are_answered_with_nothing(tmp_path):
    """No chat is a normal answer, not a failure."""
    from autoreel.chat_energy import pick_chat_log

    assert pick_chat_log(str(tmp_path)) == ""
    assert pick_chat_log(str(tmp_path / "does-not-exist")) == ""

    (tmp_path / "chat.live_chat.json").write_text("")
    assert pick_chat_log(str(tmp_path)) == "", "an empty file is not a log"


def test_pre_stream_chat_does_not_shift_every_timestamp():
    """A live fetch carries chat from BEFORE the stream, at negative
    offsets - measured at -2605s on a real one. Letting those set the
    origin would move every clip timestamp by 43 minutes."""
    from autoreel.chat_energy import _rates

    rates = _rates([-2605.0, -10.0, 0.0, 5.0, 5.4, 9.0])

    assert len(rates) == 10, "the curve starts at the stream, not at -2605s"
    assert rates[0] == 1
    assert rates[5] == 2

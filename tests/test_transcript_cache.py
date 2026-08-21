"""A transcript thrown away because the video was touched afterwards.

    [Cleanup] Kept transcript cache: clips are still to be cut from it.
    [Clips]   Nothing rendered - no transcript - the censor pass has not
              run on this video

Both lines, seconds apart, on a stream that had just been transcribed for
six minutes. The cache was there; it was being judged stale.

Staleness was "is the cache older than the video", so anything that
touched the video after transcribing discarded it - the upload, the move
into uploaded/, Windows updating an mtime on a copy. The question being
asked is "is this the same video", and a file's SIZE answers that:
touching cannot change it, and re-downloading or re-encoding always does.
"""

from __future__ import annotations

import json
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.censor import _load_cached_words, _source_stamp  # noqa: E402

WORDS = [{"start": 0.0, "end": 1.0, "text": "hi",
          "words": [{"word": "hi", "start": 0.0, "end": 0.4}]}]


def _cache(path, video, segments=WORDS, stamped=True):
    body = {"segments": segments}
    if stamped:
        body.update(_source_stamp(str(video)))
    path.write_text(json.dumps(body))


def test_touching_the_video_does_not_throw_the_transcript_away(tmp_path):
    """The actual failure: the upload touched the file and six minutes of
    transcription went in the bin."""
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 5000)
    cache = tmp_path / "stream.words.json"
    _cache(cache, video)

    time.sleep(0.01)
    os.utime(video, None)          # uploaded, moved, copied - anything

    assert _load_cached_words(str(cache), str(video)) == WORDS


def test_a_different_video_under_the_same_name_is_refused(tmp_path):
    """What staleness was actually protecting against."""
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 5000)
    cache = tmp_path / "stream.words.json"
    _cache(cache, video)

    video.write_bytes(b"y" * 9000)   # re-downloaded, different stream

    assert _load_cached_words(str(cache), str(video)) is None


def test_an_unstamped_cache_still_uses_the_old_rule(tmp_path):
    """Written before this existed - not something to vouch for blindly."""
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 5000)
    cache = tmp_path / "stream.words.json"
    _cache(cache, video, stamped=False)

    time.sleep(0.01)
    os.utime(video, None)

    assert _load_cached_words(str(cache), str(video)) is None


def test_a_stamped_cache_for_a_missing_video_is_refused(tmp_path):
    cache = tmp_path / "stream.words.json"
    cache.write_text(json.dumps({"segments": WORDS, "source_size": 5000}))

    assert _load_cached_words(str(cache), str(tmp_path / "gone.ts")) is None


def test_a_transcript_with_no_word_timings_is_still_refused(tmp_path):
    """It would find nothing to mute and nothing to caption."""
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 5000)
    cache = tmp_path / "stream.words.json"
    _cache(cache, video, segments=[{"start": 0.0, "end": 1.0, "text": "hi"}])

    assert _load_cached_words(str(cache), str(video)) is None


def test_a_broken_cache_file_is_not_a_crash(tmp_path):
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x")
    cache = tmp_path / "stream.words.json"
    cache.write_text("{ not json")

    assert _load_cached_words(str(cache), str(video)) is None


def test_a_missing_cache_is_not_a_crash(tmp_path):
    assert _load_cached_words(str(tmp_path / "gone.json"), __file__) is None


def test_the_stamp_is_written_with_the_transcript():
    """Or every cache is unstamped and nothing above applies."""
    body = open(os.path.join(_UPLOADER, "utils", "censor.py"),
                encoding="utf-8").read()

    assert "**_source_stamp(source_path)}, f)" in body

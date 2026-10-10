"""Snap-to-sound and the second listen (auto_uploader/utils/censor_verify.py)."""
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "auto_uploader"))

from autoreel.compliance import ComplianceEngine  # noqa: E402
from utils import censor_verify as cv  # noqa: E402


def _wav(path, seconds, loud=(), rate=16000):
    t = np.zeros(int(seconds * rate), dtype=np.float32)
    for a, b in loud:
        n = int((b - a) * rate)
        t[int(a * rate):int(a * rate) + n] = 0.5 * np.sin(np.arange(n) * 2 * np.pi * 220 / rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((t * 32767).astype(np.int16).tobytes())
    return str(path)


def test_mute_grows_to_where_the_word_really_is(tmp_path):
    # The word is sounding from 1.00 to 1.60 s; Whisper says 1.20-1.40.
    levels = cv.frame_levels(_wav(tmp_path / "a.wav", 3, [(1.0, 1.6)]))
    s, e = cv.snap_word(levels, 1.20, 1.40)
    assert s <= 1000 and e >= 1600
    # ...but stops near where the sound stops, not a quarter second on.
    assert s >= 1000 - 40 and e <= 1600 + 60


def test_mute_in_nonstop_talk_reaches_the_full_extra(tmp_path):
    levels = cv.frame_levels(_wav(tmp_path / "b.wav", 3, [(0.0, 3.0)]))
    s, e = cv.snap_word(levels, 1.20, 1.40)
    assert s <= 1200 - cv.SNAP_REACH_MS and e >= 1400 + cv.SNAP_REACH_MS


def test_snapping_never_shrinks_a_mute(tmp_path):
    wav = _wav(tmp_path / "c.wav", 3, [(1.25, 1.35)])
    engine = ComplianceEngine()
    found = engine.scan_words([{"word": "fuck", "start": 1.2, "end": 1.4}])
    spans = cv.snap_to_sound(wav, [(1000, 1700)], found)
    assert spans[0][0] <= 1000 and spans[-1][1] >= 1700


def test_long_files_are_reheard_around_each_mute_short_ones_in_full():
    assert cv.windows_for([(5000, 6000)], 600) == [(0.0, 600)]
    wins = cv.windows_for([(100000, 101000), (103000, 104000), (900000, 901000)], 3 * 3600)
    assert wins[0] == (97.0, 107.0) and wins[1] == (897.0, 904.0)


class _Heard:
    def __init__(self, words):
        self.words = words

    def transcribe(self, path):
        return {"segments": [{"start": 0, "end": 9, "words": self.words}] if self.words else []}


def test_second_listen_mutes_a_word_that_slipped_through(tmp_path):
    clean = _wav(tmp_path / "clean.wav", 600, [(10.0, 10.6)])
    engine = ComplianceEngine()
    # Short file: the whole thing is re-heard, so joined time == source time.
    extra, words = cv.second_listen(clean, [(2000, 2600)], engine,
                                    _Heard([{"word": "fuck", "start": 10.1, "end": 10.4}]), 600)
    assert extra and extra[0][0] <= 10100 and extra[0][1] >= 10400
    assert words == ["fuck"]


def test_second_listen_is_quiet_when_nothing_is_left(tmp_path):
    clean = _wav(tmp_path / "clean2.wav", 600)
    extra, words = cv.second_listen(clean, [(2000, 2600)], ComplianceEngine(),
                                    _Heard([{"word": "hello", "start": 1, "end": 1.2}]), 600)
    assert extra == [] and words == []


def test_a_word_heard_inside_an_existing_mute_adds_nothing(tmp_path):
    clean = _wav(tmp_path / "clean3.wav", 600)
    extra, _ = cv.second_listen(clean, [(9000, 12000)], ComplianceEngine(),
                                _Heard([{"word": "fuck", "start": 10.1, "end": 10.4}]), 600)
    assert extra == []

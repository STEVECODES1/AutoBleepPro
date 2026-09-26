"""Two quiet ways the censor pass could hand back something it should not.

1. ffmpeg wrote the censored copy straight to its final name, and the
   first thing censor_video does is reuse a finished-looking file with
   that name. A render cut short (Ctrl+C, a crash, a power cut) was
   picked up next run as "Reusing existing censored copy" and uploaded
   truncated.

2. A transcription that failed quietly - no words at all for an hour of
   talking - found no slurs, and "no slurs" returns the ORIGINAL video.
   On a stream where the real pass found 212, that is every one of them
   uploaded uncensored.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils import censor  # noqa: E402


class _Transcriber:
    hotwords = None

    def __init__(self, segments):
        self._segments = segments

    def transcribe(self, path):
        return {"segments": self._segments}


def _run(tmp_path, monkeypatch, segments, minutes):
    source = tmp_path / "stream.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(censor, "_extract_audio", lambda src, dst: None)
    monkeypatch.setattr(censor, "_get_transcriber",
                        lambda *a, **k: _Transcriber(segments))
    monkeypatch.setattr(censor, "media_duration", lambda path: minutes * 60)
    return censor.censor_video(str(source), str(tmp_path / "censored"),
                               speed={"reuse_transcript": False,
                                      "stage_timings": False})


def test_an_hour_with_no_words_is_a_failed_transcription(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="uncensored"):
        _run(tmp_path, monkeypatch, segments=[], minutes=60)


def test_a_normal_clean_transcript_still_returns_the_original(tmp_path,
                                                              monkeypatch):
    talk = [{"start": n * 10.0, "end": n * 10.0 + 9,
             "text": "okay we are going to the bank now bro come on",
             "words": []} for n in range(360)]
    result = _run(tmp_path, monkeypatch, segments=talk, minutes=60)
    assert result.was_censored is False
    assert result.output_path.endswith("stream.mp4")


def test_a_short_quiet_clip_is_not_refused(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, segments=[], minutes=0.5)
    assert result.was_censored is False


def test_an_unfinished_censored_copy_is_not_reused(tmp_path, monkeypatch):
    source = tmp_path / "stream.mp4"
    source.write_bytes(b"video")
    lengths = {str(source): 3600.0}
    monkeypatch.setattr(censor, "media_duration",
                        lambda path: lengths.get(path, 1500.0))
    assert censor._cut_short(str(tmp_path / "half.mp4"), str(source))

    lengths[str(tmp_path / "whole.mp4")] = 3599.0
    assert not censor._cut_short(str(tmp_path / "whole.mp4"), str(source))


def test_a_render_cut_short_leaves_nothing_under_the_final_name(tmp_path,
                                                                monkeypatch):
    talk = [{"start": 1.0, "end": 2.0, "text": "slur here",
             "words": [{"word": "zzbadword", "start": 1.0, "end": 1.5}]}]
    written = []

    def interrupted(source, audio, out, speed):
        written.append(out)
        with open(out, "wb") as handle:
            handle.write(b"half a video")
        raise KeyboardInterrupt

    class _Audio:
        def export(self, *a, **k):
            pass

    monkeypatch.setattr(censor, "_render", interrupted)
    monkeypatch.setattr("pydub.AudioSegment.from_wav", lambda path: _Audio())
    from autoreel.compliance import ComplianceEngine

    monkeypatch.setattr(ComplianceEngine, "censor_audio",
                        lambda self, audio, violations, method="silence": _Audio())
    with pytest.raises(KeyboardInterrupt):
        censor_dir = tmp_path / "censored"
        source = tmp_path / "stream.mp4"
        source.write_bytes(b"video")
        (censor_dir).mkdir()
        (censor_dir / "_stream_audio.wav").write_bytes(b"wav")
        monkeypatch.setattr(censor, "_extract_audio", lambda src, dst: None)
        monkeypatch.setattr(censor, "_get_transcriber",
                            lambda *a, **k: _Transcriber(talk))
        monkeypatch.setattr(censor, "media_duration", lambda path: 5.0)
        censor.censor_video(str(source), str(censor_dir),
                            custom_words=("zzbadword",),
                            speed={"reuse_transcript": False,
                                   "stage_timings": False})

    assert written and written[0].endswith(".partial.mp4")
    finals = [n for n in os.listdir(tmp_path / "censored")
              if "_CENSORED_" in n and not n.endswith(".partial.mp4")]
    assert finals == [], f"a half-written copy has the final name: {finals}"

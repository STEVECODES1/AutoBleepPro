import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from utils import daily_details  # noqa: E402


def test_report_lists_muted_words_posts_and_holds(tmp_path, monkeypatch):
    now = time.time()
    mutes = tmp_path / "mutes.jsonl"
    mutes.write_text("\n".join(json.dumps(r) for r in [
        {"at": now - 60, "file": "Stackswopo youtube live 2026-11-04 17_16.mp4",
         "scope": "all", "words": [{"t": 26.0, "word": "shit", "cat": "profanity"},
                                   {"t": 3700.0, "word": "shit", "cat": "profanity"}]},
        {"at": now - 60, "file": "Stackswopo - Yoo 11-04 - Clip 01_CENSORED_x.mp4",
         "scope": "hate_speech", "words": [{"t": 12.0, "word": "slurword", "cat": "hate_speech"}]},
    ]), encoding="utf-8")
    import utils.censor as censor
    monkeypatch.setattr(censor, "MUTES_LOG", str(mutes))
    (tmp_path / "clip_jobs.json").write_text(json.dumps({"jobs": {
        "a": {"platform": "buffer_x", "clip_path": "w/Stackswopo - Yoo 11-04 - Clip 01.mp4",
              "state": "done", "updated_at": now},
        "b": {"platform": "instagram", "clip_path": "w/Stackswopo - Yoo 11-04 - Clip 01.mp4",
              "state": "failed", "last_error": "held: 4 slurs in 20s (limit 3)", "updated_at": now},
    }}), encoding="utf-8")
    text = daily_details.build(now, str(tmp_path), str(tmp_path),
                               ["• yoooo (11-04-26) - YouTube https://y"], "Disk: 9 GB free", "Nothing needs you right now.")
    assert "MUTED WORDS" in text and "shit x2" in text and "1:01:40" in text
    assert "Clip 01" in text and "slurword" in text
    assert "on: X" in text
    assert "4 slurs" in text and "held from IG" in text

"""The whole-policy read before a YouTube upload (owner, 10-19), and the
stay-awake keeper that holds the PC up only while work is running."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader"), os.path.join(ROOT, "tools")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils import kid_guard as kg  # noqa: E402
from utils import policy_guard as pg  # noqa: E402
import stay_awake as sa  # noqa: E402


def seg(t, text):
    return {"start": float(t), "end": float(t) + 4, "text": text}


def test_pieces_are_five_minutes_with_times():
    pieces = pg.windows([seg(t, f"line {t}") for t in range(0, 900, 30)])
    assert len(pieces) == 3 and pieces[0][2].startswith("[0] line 0")


def test_parse_clamps_pads_and_drops_unknown():
    reply = ('ok {"flags": [{"category": "harassment", "severity": "cut", "start": 100, '
             '"end": 120, "why": "address read out"}, {"category": "swearing", "severity": "cut"}]}')
    flags = pg.parse(reply, 90, 400)
    assert flags == [{"category": "harassment", "severity": "cut", "start": 95.0,
                      "end": 125.0, "why": "address read out"}]
    assert pg.parse("no json here", 0, 10) == []


def test_review_falls_through_a_silent_model():
    reply = '{"flags": [{"category": "hate", "severity": "warn", "start": 5, "end": 9, "why": "x"}]}'
    askers = [("dead", lambda p: ""), ("live", lambda p: reply)]
    flags, note = pg.review([seg(0, "hello"), seg(10, "bye")], askers=askers)
    assert [f["category"] for f in flags] == ["hate"] and note == ""


def test_review_without_models_says_so():
    assert pg.review([seg(0, "x")], askers=[]) == ([], "no AI model configured - policy review skipped")


def _run(monkeypatch, flags):
    said, cuts = [], []
    monkeypatch.setattr(kg, "_segments", lambda cfg, *p: [seg(0, "gg"), seg(600, "later")])
    monkeypatch.setattr(kg, "_duration", lambda p: 3600)
    monkeypatch.setattr(kg, "_policy", lambda segs: (flags, ""))
    monkeypatch.setattr(kg, "cut", lambda src, c, out, d: cuts.append(c) or True)
    out = kg.make_safe(None, "v.mp4", "v_C.mp4", "o", alert=lambda t, m, urgent=True: said.append((t, m)))
    return out, said, cuts


def test_a_cloud_child_safety_call_is_cut(monkeypatch):
    (path, why), said, cuts = _run(monkeypatch, [{"category": "child_safety", "severity": "cut",
                                                   "start": 100, "end": 130, "why": "minor",
                                                   "model": "gemini:flash"}])
    assert path and why == "" and cuts[0][0][:2] == (100, 130)
    assert "child_safety: minor" in said[0][1]


def test_other_ai_calls_and_local_model_calls_are_only_reported(monkeypatch):
    flags = [{"category": "harassment", "severity": "cut", "start": 100, "end": 130,
              "why": "game threat", "model": "gemini:flash"},
             {"category": "child_safety", "severity": "cut", "start": 300, "end": 320,
              "why": "unsure", "model": "local:qwen"}]
    (path, why), said, cuts = _run(monkeypatch, flags)
    assert (path, why, cuts) == (None, "", []) and "game threat" in said[0][1]


def test_warn_only_is_reported_not_cut(monkeypatch):
    (path, why), said, cuts = _run(monkeypatch, [{"category": "hate", "severity": "warn",
                                                   "start": 50, "end": 60, "why": "borderline"}])
    assert (path, why, cuts) == (None, "", []) and "borderline" in said[0][1]


def test_keeper_is_busy_only_while_work_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "ROOT", str(tmp_path))
    assert sa.busy_reason(ffmpeg=lambda: False, recording=lambda: False) == ""
    assert sa.busy_reason(ffmpeg=lambda: False, recording=lambda: True) == "recording"
    logs = tmp_path / "auto_uploader" / "logs"
    logs.mkdir(parents=True)
    (logs / "processing.json").write_text('{"pid": 4242}')
    assert sa.busy_reason(ffmpeg=lambda: False, recording=lambda: False,
                          alive=lambda pid: pid == 4242) == "processing"
    assert sa.busy_reason(ffmpeg=lambda: False, recording=lambda: False,
                          alive=lambda pid: False) == ""
    (logs / "uploading.json").write_text("{}")
    assert sa.busy_reason(ffmpeg=lambda: False, recording=lambda: False) == "uploading"

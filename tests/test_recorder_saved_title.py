"""A recording finished by a later recorder run keeps its title and id
(11-02: "its time" went up as "STACKSWOPO YOUTUBE LIVE")."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import record_stream as rs  # noqa: E402


def test_saved_title_drops_ytdlp_time_stamp(tmp_path):
    (tmp_path / "S 2026-11-02 16_29.txt").write_text(
        "its time 2026-11-02 17:49\n", encoding="utf-8")
    assert rs.saved_title(str(tmp_path), "S 2026-11-02 16_29") == "its time"


def test_saved_title_missing_is_empty(tmp_path):
    assert rs.saved_title(str(tmp_path), "nope") == ""


def test_saved_video_id_from_log(tmp_path):
    (tmp_path / "S.log").write_text(
        "[youtube:tab] @x/live: Downloading webpage\n"
        "[youtube] eOFnN9GMPak: Downloading webpage\n", encoding="utf-8")
    assert rs.saved_video_id(str(tmp_path), "S") == "eOFnN9GMPak"


def test_recovered_title_does_not_leak_to_next_stream(tmp_path, monkeypatch):
    (tmp_path / "B.txt").write_text("its time\n", encoding="utf-8")
    seen = {}

    class Fake:
        staging = str(tmp_path)
        title = ""
        video_id = ""
        finalise = rs.StreamRecorder.finalise if hasattr(rs, "StreamRecorder") else None

        def _finalise(self, base):
            seen["title"] = self.title
            return "x"

    rec_cls = next(c for c in vars(rs).values()
                   if isinstance(c, type) and "_finalise" in vars(c))
    fake = Fake()
    assert rec_cls.finalise(fake, "B") == "x"
    assert seen["title"] == "its time"
    assert fake.title == ""

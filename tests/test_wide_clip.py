import os

from autoreel import wide_clip


def test_x_cut_found_under_any_name_of_the_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(wide_clip, "WIDE_DIR", str(tmp_path))
    made = wide_clip.path_for(r"C:\w\Stackswopo - Yoo 11-04 - Clip 01.mp4")
    with open(made, "wb") as f:
        f.write(b"x")
    for name in (r"C:\q\Stackswopo - Yoo 11-04 - Clip 01.mp4",
                 r"C:\c\_vertical_Stackswopo - Yoo 11-04 - Clip 01.mp4",
                 r"C:\c\Stackswopo - Yoo 11-04 - Clip 01_CENSORED_silence-x.mp4"):
        assert wide_clip.find(name) == made
    assert wide_clip.find(r"C:\w\Stackswopo - Yoo 11-04 - Clip 02.mp4") == ""


def test_x_cut_has_its_own_name():
    assert "XCUT" in os.path.basename(wide_clip.path_for("Clip 01.mp4"))

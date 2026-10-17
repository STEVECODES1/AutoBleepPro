"""Garbled emoji ("ðŸ“Œ") must never reach a viewer again.

On 10-10 a PowerShell edit of config.json garbled three templates and five
days of descriptions and captions went out that way. The config is now
repaired on load and every outbound route checks its text.
"""
import json
import os

import pytest

UPLOADER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "auto_uploader")
PIN = "\U0001F4CC Uncut on Rumble"
GARBLED = PIN.encode("utf-8").decode("cp1252")


def test_shipped_example_config_is_clean():
    from utils.unmojibake import fix_tree
    with open(os.path.join(UPLOADER, "config.example.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert fix_tree(data) == data


def test_load_config_repairs_a_garbled_template(tmp_path, capsys):
    from utils.config import load_config
    with open(os.path.join(UPLOADER, "config.example.json"), encoding="utf-8") as f:
        data = json.load(f)
    data["youtube"]["description_template"] = f"[STREAM TITLE]\n\n{GARBLED}"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cfg = load_config(str(path), env_path=str(tmp_path / ".env"))
    assert PIN in cfg.youtube.description_template
    assert GARBLED not in cfg.youtube.description_template
    assert "garbled" in capsys.readouterr().out


def test_social_promoter_text_is_checked():
    from utils import social_promoter
    assert hasattr(social_promoter.build_caption, "unchecked")
    assert hasattr(social_promoter.build_message, "unchecked")
    out = social_promoter.build_caption(GARBLED + " {title}", "x.mp4", title="t")
    assert GARBLED not in out


# Every route passes its text through utils/text_guard.py, which runs the
# mojibake repair first and then drops the other never-meant-to-be-seen
# bits (a dangling "(10-09-26 to )", empty brackets, unfilled tokens).
@pytest.mark.parametrize("module,needle", [
    ("utils/youtube_uploader.py", "title = _guard.title(title"),
    ("utils/youtube_uploader.py", "description = _guard.youtube_description(description)"),
    ("utils/youtube_uploader.py", "text = _guard.text(str(text), \"comment\")"),
    ("utils/recap.py", "title = _guard.title(title"),
    ("utils/recap.py", "description = _guard.youtube_description(description"),
    ("utils/rumble_uploader.py", "title = _guard.title(title"),
    ("utils/rumble_uploader.py", "description = _guard.text(description"),
    ("utils/clip_queue.py", "post_clip(upload, _guard.text(caption"),
    ("publishers/postproxy.py", "else _guard.text(value, name)"),
    ("utils/social_promoter.py", "return text_guard.text(build(*args, **kwargs)"),
])
def test_every_outbound_route_checks_its_text(module, needle):
    with open(os.path.join(UPLOADER, module), encoding="utf-8") as f:
        assert needle in f.read()

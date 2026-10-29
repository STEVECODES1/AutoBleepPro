import json, os, subprocess, sys, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
import live_chat_overlay as o  # noqa: E402

LINE = json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {
    "liveChatTextMessageRenderer": {"authorName": {"simpleText": "@fan123"},
                                    "message": {"runs": [{"text": "W stream "}, {"emoji": {}},
                                                         {"text": "lol"}]}}}}}]}})


def test_a_chat_line_becomes_author_and_text():
    assert o.messages_in(LINE) == [("@fan123", "W stream lol")]
    assert o.messages_in("not json") == []


def test_render_wraps_and_keeps_the_newest():
    text = o.render([("a", "x" * 80)] + [(f"u{i}", f"msg {i}") for i in range(12)])
    assert text.startswith("YOUTUBE CHAT") and "u11: msg 11" in text and "u0:" not in text
    assert all(len(l) <= o.WIDTH for l in text.splitlines()[1:])


def test_the_filter_parses_in_ffmpeg(tmp_path):
    if not shutil.which("ffmpeg"):
        return
    f = tmp_path / "chat.txt"
    f.write_text(o.render([("fan", "hello there")]), encoding="utf-8")
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=1",
                        "-vf", "fps=30," + o.drawtext(str(f)), "-f", "null", "-"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_chat_file_has_plain_newlines(tmp_path):
    feed = o.ChatFeed.__new__(o.ChatFeed)
    feed.text_file = str(tmp_path / "c.txt")
    feed._write("a\nb")
    assert open(feed.text_file, "rb").read() == b"a\nb"

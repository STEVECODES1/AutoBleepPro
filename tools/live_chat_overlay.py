"""The YouTube live chat, drawn top-right on the Rumble relay (owner, 10-29:
"have the youtube live chat on the screen of the rumble live... top right
like a streamer").

ChatFeed runs yt-dlp's live-chat download for the stream (no video), tails
the JSON lines it writes, and keeps the newest few messages in a small text
file. The relay's ffmpeg draws that file with drawtext (reload=1), so new
messages appear within a second and nothing in the video pipeline waits on
chat. No chat (yt-dlp fails, chat off) = an empty box is never drawn: the
file just stays empty.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time

LINES = 8          # messages shown
WIDTH = 34         # characters per line before wrapping
FONT_SIZE = 20     # at 720p


def _text_of(runs) -> str:
    out = []
    for r in runs or []:
        if "text" in r:
            out.append(r["text"])
        elif "emoji" in r:
            # Custom/Unicode emoji: drawtext's font has none - a short tag.
            out.append("")
    return "".join(out)


def messages_in(line: str):
    """(author, text) for every chat message in one yt-dlp live_chat line."""
    try:
        data = json.loads(line)
    except ValueError:
        return []
    found = []

    def walk(node):
        if isinstance(node, dict):
            r = node.get("liveChatTextMessageRenderer")
            if isinstance(r, dict):
                author = (r.get("authorName") or {}).get("simpleText", "")
                text = _text_of((r.get("message") or {}).get("runs"))
                if text.strip():
                    found.append((author, text))
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)
    return found


def plain(s: str) -> str:
    """Only what the overlay font can draw: no emoji, no control chars."""
    return "".join(ch for ch in s if 32 <= ord(ch) < 0x2000 or ch in "\u2019\u201c\u201d")


def render(messages) -> str:
    """The overlay text: newest at the bottom, each message wrapped."""
    lines = []
    for author, text in messages[-LINES:]:
        who = plain(author).strip().lstrip("@")[:18]
        body = plain(text).strip()
        if not body:
            continue
        wrapped = textwrap.wrap(f"{who}: {body}", WIDTH) or [""]
        lines.extend(wrapped[:3])
    return "\n".join(["YOUTUBE CHAT"] + lines[-(LINES + 4):])


def ff_path(path: str) -> str:
    """A path inside an ffmpeg filter argument (Windows drive colon escaped)."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def font_file() -> str:
    for f in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.isfile(f):
            return f
    return ""


def drawtext(text_file: str) -> str:
    """The drawtext filter for the chat box, top-right."""
    font = font_file()
    font_opt = f"fontfile='{ff_path(font)}':" if font else ""
    return (f"drawtext={font_opt}textfile='{ff_path(text_file)}':reload=15:"
            f"fontsize={FONT_SIZE}:fontcolor=white:line_spacing=6:"
            f"box=1:boxcolor=black@0.55:boxborderw=12:"
            f"x=w-tw-30:y=30")


class ChatFeed(threading.Thread):
    """Keeps text_file holding the newest chat of `video_id`."""

    def __init__(self, video_id: str, text_file: str, py: str = sys.executable):
        super().__init__(daemon=True)
        self.video_id = video_id
        self.text_file = text_file
        self.py = py
        self.work = tempfile.mkdtemp(prefix="livechat_")
        self.proc = None
        self.stopped = False
        self.messages = []
        self._write("YOUTUBE CHAT")

    def _write(self, text: str) -> None:
        tmp = self.text_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.text_file)
        except OSError:
            pass   # ffmpeg may hold it a moment; the next message rewrites it

    def _start(self):
        self.proc = subprocess.Popen(
            [self.py, "-m", "yt_dlp", "--skip-download", "--no-warnings",
             "--write-subs", "--sub-langs", "live_chat",
             "-o", os.path.join(self.work, "%(id)s.%(ext)s"),
             f"https://www.youtube.com/watch?v={self.video_id}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def run(self):
        pos = 0
        path = ""
        while not self.stopped:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
                pos, path = 0, ""
            if not path:
                got = glob.glob(os.path.join(self.work, "*live_chat*"))
                path = got[0] if got else ""
            if path and os.path.exists(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                except OSError:
                    chunk = ""
                new = []
                for line in chunk.splitlines():
                    new += messages_in(line)
                if new:
                    self.messages = (self.messages + new)[-LINES:]
                    self._write(render(self.messages))
            time.sleep(1.0)

    def stop(self):
        self.stopped = True
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass

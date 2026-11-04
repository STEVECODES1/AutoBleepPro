"""Every Discord message from the bot goes through here (owner 11-04: "when
text comes like this it can get my discord banned - send it as a txt file").

- clean(): slurs and strong words are starred out ("n****") - in the
  message AND in any attached file.
- send(): the message stays short (a title and a line or two). Anything long
  - the brain's lists, cut lists, logs - is attached as a .txt file.
Never raises; returns True when Discord took it.
"""

import json
import re
import urllib.request
import uuid

SHORT = 350          # longer than this -> the rest goes in the attachment
_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        try:
            from autoreel.compliance import ComplianceEngine
            from utils.clip_queue import scope_allow, scope_categories

            _ENGINE = ComplianceEngine(only_categories=scope_categories("watchable"),
                                       allow_words=scope_allow("watchable"))
        except Exception:
            _ENGINE = False
    return _ENGINE


def _star(word: str) -> str:
    letters = [i for i, c in enumerate(word) if c.isalpha()]
    if len(letters) < 2:
        return word
    keep = letters[0]
    return "".join(c if (i == keep or not c.isalpha()) else "*" for i, c in enumerate(word))


def clean(text: str) -> str:
    """The text with every flagged word starred out. Unknown engine -> text."""
    engine = _engine()
    if not engine or not text:
        return text or ""

    def fix(m):
        word = m.group(0)
        try:
            return _star(word) if engine._flag_reason(word) else word
        except Exception:
            return word
    return re.sub(r"[A-Za-z][A-Za-z']+", fix, text)


def send(url: str, title: str, body: str = "", username: str = "AutoBleep",
         filename: str = "details.txt", mention_user: str = "", post=None) -> bool:
    if not url:
        return False
    title, body = clean(title or ""), clean(body or "")
    short = body if len(body) <= SHORT else body[:SHORT].rsplit("\n", 1)[0]
    attach = body if len(body) > SHORT else ""
    mention = f"<@{mention_user}> " if mention_user and mention_user.isdigit() else ""
    content = f"{mention}**{title}**" + (f"\n{short}" if short else "")
    if attach:
        content += "\n📎 Full details in the attached file."
    payload = {"username": username, "content": content[:1900],
               "allowed_mentions": ({"users": [mention_user]} if mention else {"parse": []})}
    try:
        if not attach:
            data = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json", "User-Agent": "AutoBleep"}
        else:
            boundary = uuid.uuid4().hex
            parts = [
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
                f"Content-Type: application/json\r\n\r\n{json.dumps(payload)}\r\n",
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
                f"filename=\"{filename}\"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n",
            ]
            data = (parts[0].encode("utf-8") + parts[1].encode("utf-8")
                    + (title + "\n\n" + body).encode("utf-8")
                    + f"\r\n--{boundary}--\r\n".encode("utf-8"))
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}",
                       "User-Agent": "AutoBleep"}
        if post is not None:
            return bool(post(url, data, headers))
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception:
        return False

"""Undo cp1252 mojibake: UTF-8 bytes that were read as Windows-1252.

"âœ‚ï¸" back to the scissors emoji, "ðŸ“Œ" back to the pin. Text that is
already right is returned unchanged: each run of non-ASCII characters is
turned back into bytes and decoded as UTF-8, and a run that does not
decode cleanly (a real "é", a real emoji) is left exactly as it was.

    python tools/unmojibake.py --config      fix auto_uploader/config.json in place
"""
import json
import os
import re
import sys
import unicodedata

_RUN = re.compile(r"[^\x00-\x7f]+")


def _to_bytes(run):
    out = bytearray()
    for ch in run:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            if ord(ch) < 256:
                out.append(ord(ch))      # the five bytes cp1252 leaves undefined
            else:
                return None              # a real non-Latin character: not mojibake
    return bytes(out)


# cp1252 has no character for these five bytes, so they came through as
# control characters (U+008F...) - and YouTube strips control characters.
# A posted "✂️" (E2 9C 82 EF B8 8F) arrives as "âœ‚ï¸" with the 8F gone,
# which no longer decodes. One lost byte per character is put back.
_LOST = (0x8F, 0x81, 0x8D, 0x90, 0x9D)


def _seq_len(lead):
    if 0xC2 <= lead <= 0xDF:
        return 2
    if 0xE0 <= lead <= 0xEF:
        return 3
    if 0xF0 <= lead <= 0xF4:
        return 4
    return 1


def _decode(raw, repairs=4):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        start = exc.start
    need = _seq_len(raw[start])
    if repairs <= 0 or need < 3:
        return None                      # only emoji/symbol sequences are repaired
    # last byte first: emoji leads (F0 9F, E2) are never among the lost five
    for at in range(min(start + need - 1, len(raw)), start, -1):
        for byte in _LOST:
            cand = raw[:at] + bytes([byte]) + raw[at:]
            try:
                char = cand[start:start + need].decode("utf-8")
            except UnicodeDecodeError:
                continue
            if ord(char) < 0x2000 or unicodedata.category(char) in ("Co", "Cn", "Cs"):
                continue                 # not a symbol/emoji: wrong spot for the byte
            out = _decode(cand, repairs - 1)
            if out is not None:
                return out
    return None


def _fix_run(match):
    run = match.group(0)
    raw = _to_bytes(run)
    if raw is None:
        return run
    fixed = _decode(raw)
    return fixed if fixed is not None else run


def fix(text):
    if not isinstance(text, str) or text.isascii():
        return text
    prev = None
    while prev != text:                  # twice-garbled text needs two passes
        prev, text = text, _RUN.sub(_fix_run, text)
    return text


def fix_tree(node):
    if isinstance(node, dict):
        return {k: fix_tree(v) for k, v in node.items()}
    if isinstance(node, list):
        return [fix_tree(v) for v in node]
    return fix(node)


def main():
    if "--config" not in sys.argv:
        print(__doc__)
        return 0
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "auto_uploader", "config.json")
    with open(path, "rb") as f:
        raw = f.read()
    data = json.loads(raw.decode("utf-8-sig"))
    fixed = fix_tree(data)
    if fixed == data:
        print("config.json: clean already")
        return 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(fixed, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("config.json: fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

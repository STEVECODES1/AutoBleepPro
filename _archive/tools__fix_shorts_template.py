"""One-time repair: the Shorts description template holds mojibake.

Each emoji was saved as its UTF-8 bytes misread as cp1252 (F0 9F 8E AE
became four chars), so YouTube prints the wreckage. This replaces each
4-char run with the real codepoint. Keys are built from code numbers,
not literals: a literal mojibake string in source is one editor save
away from becoming mojibake itself.

Run: python tools/fix_shorts_template.py
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "auto_uploader", "config.json")


def _run(*codes):
    return "".join(map(chr, codes))


# mojibake run -> real emoji: target, pin, red circle.
FIXES = {
    _run(0xF0, 0x178, 0x17D, 0xAE): _run(0x1F3AF),
    _run(0xF0, 0x178, 0x201C, 0x152): _run(0x1F4CC),
    _run(0xF0, 0x178, 0x201D, 0xB4): _run(0x1F534),
}


def main() -> int:
    with io.open(PATH, encoding="utf-8") as handle:
        config = json.load(handle)
    template = config.get("youtube_shorts", {}).get("description_template", "")
    fixed = template
    for broken, whole in FIXES.items():
        fixed = fixed.replace(broken, whole)
    if fixed == template:
        print("template already clean")
        return 0
    config["youtube_shorts"]["description_template"] = fixed
    with io.open(PATH, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print("repaired")
    return 0


if __name__ == "__main__":
    sys.exit(main())

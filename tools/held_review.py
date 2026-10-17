"""Watch held clips in Discord and post the good ones - logic in auto_uploader/utils/held_review.py.

    python -X utf8 tools/held_review.py --send
    python -X utf8 tools/held_review.py --post 3 7
    python -X utf8 tools/held_review.py --list
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
sys.path.insert(0, ROOT)
os.chdir(os.path.join(ROOT, "auto_uploader"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, "auto_uploader", ".env"))
except ImportError:
    pass

from utils import held_review  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--post", nargs="+", type=int, default=[])
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.post:
        print(f"{held_review.post(args.post)} job(s) back in the queue - the uploader posts them.")
    if args.send:
        print(f"{held_review.send_new()} clip(s) sent to Discord.")
    if args.list or not (args.post or args.send):
        for name, info in held_review.held_clips().items():
            print(f"{info['why'][:40]:40s}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

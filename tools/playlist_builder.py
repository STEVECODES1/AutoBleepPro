"""Bring the wopovod playlists up to date now - the logic is auto_uploader/utils/playlists.py.

    python -X utf8 tools/playlist_builder.py              sync (stops at 800 units)
    python -X utf8 tools/playlist_builder.py --cap 3000
    python -X utf8 tools/playlist_builder.py --dry-run    what it would add/remove, ~20 units

The uploader already does this by itself once a week, after a stream's
recap, so this is only for doing it sooner.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
os.chdir(os.path.join(ROOT, "auto_uploader"))

from utils import playlists  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=800)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    spent, finished = playlists.sync(cap=args.cap, dry_run=args.dry_run)
    for key, pid in sorted(playlists._load().get("ids", {}).items()):
        print(f"  {key:7s} https://www.youtube.com/playlist?list={pid}")
    return 0 if finished else 1


if __name__ == "__main__":
    sys.exit(main())

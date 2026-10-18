"""Post a full stream on wopovod's Posts tab - what the uploader does by
itself after each new full stream (auto_uploader/utils/community_post.py).

    python -X utf8 tools/community_post.py <video link or id> "<stream title>"
        [--dry-run] [--wait S] [--tries N] [--cdp URL]

--dry-run fills the post box and attaches the video, then stops short of
Post. One post per video, ever (auto_uploader/logs/community_posts.json).
"""

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils.community_post import post, video_id, wait_until_processed  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Post a full stream on wopovod's Posts tab.")
    ap.add_argument("video", help="watch link or 11-character id")
    ap.add_argument("title", help="the stream's name, as on the channel")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait", type=float, default=0, help="seconds to wait first")
    ap.add_argument("--tries", type=int, default=1)
    ap.add_argument("--between", type=float, default=600)
    ap.add_argument("--cdp", default="http://localhost:9222")
    ap.add_argument("--max-hours", type=float, default=12,
                    help="how long to wait for YouTube to finish processing")
    args = ap.parse_args(argv)
    print(time.strftime("%m-%d %H:%M:%S"), "post for", args.video, flush=True)
    if args.wait:
        time.sleep(args.wait)
    # A post can only carry a finished video (10-18).
    if not args.dry_run and not wait_until_processed(video_id(args.video),
                                                     max_hours=args.max_hours):
        print("result: failed", flush=True)
        return 1
    print(time.strftime("%m-%d %H:%M:%S"), "video is processed - posting", flush=True)
    for attempt in range(max(1, args.tries)):
        result = post(args.video, args.title, cdp_url=args.cdp, dry_run=args.dry_run)
        if result:
            print("result:", result, flush=True)
            return 0
        if attempt + 1 < args.tries:
            time.sleep(args.between)
    print("result: failed", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())

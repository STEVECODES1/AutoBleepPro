"""Make a story episode for STACKSWOPOVODS from one stream (utils/stories).

    python -X utf8 tools/story_episode.py "<stream file>" "<stream title>" [date] --dry-run
    python -X utf8 tools/story_episode.py "<stream file>" "<stream title>" [date]

--dry-run only asks the AI which storyline it would pick and prints it.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
os.chdir(os.path.join(ROOT, "auto_uploader"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("title")
    ap.add_argument("date", nargs="?", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    from utils.config import load_config
    from utils import stories
    from utils.ffmpeg_tools import media_duration
    from utils.recap import transcript_of

    cfg = load_config()
    s = stories.settings(cfg)
    if a.dry_run:
        segs = transcript_of(cfg, a.file)
        dur = media_duration(a.file) or 0.0
        picked = stories.pick(segs, dur, max(2, s["per_stream"]), s["min_s"], s["max_s"])
        for p in picked:
            print(f"{p['start'] / 60:6.1f}-{p['end'] / 60:6.1f} min  {stories.story_title(p['title'])}"
                  f"  [thumb: {p['thumb']}]  ({p.get('model')}) {p['why']}")
        if not picked:
            print("no story picked")
        return 0
    from main import find_intro
    urls = stories.make(cfg, a.file, a.title, a.date, intro=find_intro(cfg))
    print("\n".join(urls) or "nothing uploaded")
    return 0 if urls else 1


if __name__ == "__main__":
    sys.exit(main())

"""Check every title and description already up on the four YouTube channels.

    python -X utf8 tools/metadata_audit.py                 report only (about 1 unit per 50 videos)
    python -X utf8 tools/metadata_audit.py --fix --max 20  fix up to 20, public first

Uses the same rules as utils/text_guard.py, the check every new upload
passes through, so what this reports is exactly what the guard would
have caught. --fix rewrites only the title/description text; tags,
category and languages go back unchanged. Every change is appended to
logs/metadata_audit.jsonl WITH the old text, so any one can be put back.
A videos.update is 50 units of the shared 10,000/day, so --max is small.
"""
import argparse
import collections
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from rumble_cta_backfill import CHANNELS, UPLOADER, service, update, uploads  # noqa: E402
from utils import text_guard  # noqa: E402

LOG = os.path.join(UPLOADER, "logs", "metadata_audit.jsonl")


def check(video):
    sn = video["snippet"]
    title, desc = sn.get("title", ""), sn.get("description", "")
    found = [f"title: {p}" for p in text_guard.problems(title, is_title=True, youtube=True)]
    found += [f"description: {p}" for p in text_guard.problems(desc, youtube=True)]
    return found


def fixed(video):
    sn = video["snippet"]
    title, desc = sn.get("title", ""), sn.get("description", "")
    new_title = text_guard.title(title, fallback=title)
    new_desc = text_guard.youtube_description(desc)
    return (new_title, new_desc) if (new_title, new_desc) != (title, desc) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--max", type=int, default=20)
    ap.add_argument("--channel", default="")
    ap.add_argument("--show", type=int, default=4, help="examples per problem")
    args = ap.parse_args()

    done = 0
    for label, token in CHANNELS:
        if args.channel and args.channel.lower() not in label.lower():
            continue
        svc = service(token)
        name, videos = uploads(svc)
        public = [v for v in videos if v.get("status", {}).get("privacyStatus") == "public"]
        by_problem = collections.defaultdict(list)
        flagged = []
        for v in videos:
            found = check(v)
            if found:
                flagged.append(v)
            for p in found:
                key = p.split(": '", 1)[0] if "link label" in p else p
                by_problem[key].append((v, p))
        print(f"== {label} ({name}): {len(videos)} videos ({len(public)} public), "
              f"{len(flagged)} with something to tidy")
        for problem, hits in sorted(by_problem.items(), key=lambda kv: -len(kv[1])):
            print(f"   {len(hits):4d}  {problem}")
            for v, p in hits[:args.show]:
                state = v.get("status", {}).get("privacyStatus", "?")[:3]
                print(f"         [{state}] {v['id']}  {v['snippet']['title'][:80]}")
        if not args.fix:
            continue
        # Public only: nobody sees a private description, and private and
        # unlisted uploads include old ones that are not this pipeline's.
        flagged = [v for v in flagged if v.get("status", {}).get("privacyStatus") == "public"]
        for v in flagged:
            if done >= args.max:
                break
            plan = fixed(v)
            if not plan:
                continue
            try:
                update(svc, v, *plan)
            except Exception as exc:
                print(f"  STOP at {v['id']}: {str(exc)[:200]}")
                return 1
            done += 1
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.time(), "channel": label, "id": v["id"],
                                    "old_title": v["snippet"]["title"],
                                    "old_description": v["snippet"].get("description", ""),
                                    "new_title": plan[0]}, ensure_ascii=False) + "\n")
            print(f"  fixed {v['id']}: {plan[0][:80]}")
    if args.fix:
        print(f"fixed {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

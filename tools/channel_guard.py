"""Did YouTube strip a "(Fan Channel)" name or the About notice?

The clips channel was removed on 10-18 for looking like Stacks' own
channel. Everything local stayed healthy, so nothing here noticed until
the channel was already gone. This is the outside check: four read-only
API calls, once a day, and a Discord/ntfy alert if a name or About no
longer says fan + actors.

The uploader's watchdog runs the same check once a day, but only while
the uploader is alive. This script is for a Windows scheduled task, so a
dead uploader does not also mean a blind fan-label check.

    python -X utf8 tools/channel_guard.py [--dry-run] [--no-alert]

Exit 0 when all four are correct, 1 when any is wrong, 2 when it could
not check at all.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADER = os.path.join(ROOT, "auto_uploader")
sys.path.insert(0, ROOT)
sys.path.insert(0, UPLOADER)

from utils.brain import channel_problems, send_alert  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print the result, send no alerts")
    ap.add_argument("--no-alert", action="store_true",
                    help="send no alerts (same check, quiet)")
    a = ap.parse_args()

    os.chdir(UPLOADER)
    try:
        from utils.config import load_config

        cfg = load_config()
    except Exception as exc:
        print(f"Could not load config: {exc}")
        return 2

    secrets = str(getattr(getattr(cfg, "youtube", None),
                          "client_secrets_path", "") or "")
    if not secrets or not os.path.isfile(secrets):
        print("No YouTube client_secrets.json - cannot check the channels.")
        return 2

    problems = channel_problems(cfg)
    if not problems:
        print("All four channels still say Fan Channel and carry the "
              "fan + actors notice.")
        return 0

    for key, (title, message) in sorted(problems.items()):
        print(f"PROBLEM {key}: {title} - {message}")
        if not (a.dry_run or a.no_alert):
            try:
                reached = send_alert(title, message, urgent=True)
                print(f"  alert sent to: {', '.join(reached) or 'nowhere'}")
            except Exception as exc:
                print(f"  alert failed: {exc}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

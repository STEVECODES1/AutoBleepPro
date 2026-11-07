"""Run tools/post_links.py every 15 minutes while the uploader watches
(11-04: a link for every post, one tidy Discord card per clip). Its own
process each time, so a slow platform API never blocks an upload."""

import os
import subprocess
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL = os.path.join(_ROOT, "tools", "post_links.py")
LOG = os.path.join(_ROOT, "auto_uploader", "logs", "post_links.log")
EVERY_S = 15 * 60


def run_once() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST") or not os.path.isfile(TOOL):
        return
    try:
        with open(LOG, "a", encoding="utf-8") as log:
            subprocess.run([sys.executable, "-X", "utf8", TOOL], stdout=log,
                           stderr=subprocess.STDOUT, cwd=_ROOT, timeout=600)
    except Exception:
        pass


def weekly_top5() -> None:
    """The weekly ranked "Top 5" Short (utils/top5.py), when one is due."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        from utils import top5

        app = os.path.join(_ROOT, "auto_uploader")
        folders = [os.path.join(app, d) for d in
                   ("clip_queue_files", "watch_folder", "uploaded", "censored")]
        top5.maybe_make(os.path.join(app, "watch_folder"), folders)
    except Exception:
        pass


def start(first_delay_s: float = 120) -> threading.Thread:
    def loop():
        time.sleep(first_delay_s)
        while True:
            run_once()
            weekly_top5()
            try:
                from utils.binscripts_weekly import start_if_due
                start_if_due()
            except Exception:
                pass
            time.sleep(EVERY_S)

    t = threading.Thread(target=loop, name="link-cards", daemon=True)
    t.start()
    return t

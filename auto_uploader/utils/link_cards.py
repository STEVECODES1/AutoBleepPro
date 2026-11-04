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


def start(first_delay_s: float = 120) -> threading.Thread:
    def loop():
        time.sleep(first_delay_s)
        while True:
            run_once()
            time.sleep(EVERY_S)

    t = threading.Thread(target=loop, name="link-cards", daemon=True)
    t.start()
    return t

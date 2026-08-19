"""Run the uploader from the repository root.

`main.py` lives in auto_uploader/, and every instruction that forgets to
say so produces:

    python: can't open file 'D:\\AutoBleepPro-git\\main.py':
    [Errno 2] No such file or directory

which is a dead end at the exact moment somebody is trying to change one
setting. The root of the repo is where anyone lands after a `git pull`,
so it is where the command should work.

Not a copy of anything: it hands off to the real main.py with the same
arguments, from that folder, so config.json, .env and every relative
path resolve exactly as they do when it is run directly. There is one
program here and this is a door into it.
"""

import os
import runpy
import sys

REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "auto_uploader", "main.py")


def main() -> int:
    folder = os.path.dirname(REAL)
    # The real main.py resolves config.json and .env against its own
    # folder, but subprocesses and relative paths follow the working
    # directory - so move, rather than only adding to sys.path.
    os.chdir(folder)
    sys.path.insert(0, folder)
    # argv[0] becomes the real script, so --help prints its name and not
    # this one's.
    sys.argv[0] = REAL
    runpy.run_path(REAL, run_name="__main__")
    return 0


if __name__ == "__main__":
    if not os.path.isfile(REAL):
        print(f"[AutoBleep] Cannot find {REAL} - is this a full checkout?")
        raise SystemExit(1)
    raise SystemExit(main())

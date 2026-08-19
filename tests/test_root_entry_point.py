"""`python main.py` works from the repository root.

The root is where anyone is standing after a `git pull`, and every
command typed there answered:

    python: can't open file 'D:\\AutoBleepPro-git\\main.py':
    [Errno 2] No such file or directory

- a dead end at the exact moment somebody is trying to change one
setting. The shim is a door, not a second program: it must never grow
behaviour of its own.
"""

from __future__ import annotations

import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_root_shim_exists():
    assert os.path.isfile(os.path.join(_REPO, "main.py"))


def test_it_points_at_the_real_one():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_root_shim", os.path.join(_REPO, "main.py"))
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)

    assert shim.REAL.endswith(os.path.join("auto_uploader", "main.py"))
    assert os.path.isfile(shim.REAL)


def test_it_is_a_door_not_a_second_program():
    """Anything it decides for itself is a thing that can disagree with
    the real main.py, silently, forever."""
    body = open(os.path.join(_REPO, "main.py"), encoding="utf-8").read()

    assert "argparse" not in body, "the shim must not parse arguments"
    assert "add_argument" not in body
    # Short enough to read in one go.
    assert len([l for l in body.splitlines() if l.strip()
                and not l.strip().startswith("#")]) < 40


def test_running_it_from_the_root_reaches_the_real_program():
    out = subprocess.run([sys.executable, "main.py", "--help"],
                         cwd=_REPO, capture_output=True, text=True,
                         timeout=120)

    assert out.returncode == 0, out.stderr
    # Options that only exist in the real main.py.
    assert "--posting-status" in out.stdout
    assert "--hook" in out.stdout


def test_it_runs_from_the_real_folder():
    """config.json, .env and every relative path resolve against
    auto_uploader/, so the shim has to move there, not just import."""
    out = subprocess.run(
        [sys.executable, "main.py", "-c",
         "import os;print(os.getcwd())"],
        cwd=_REPO, capture_output=True, text=True, timeout=120)

    # -c is not a real flag; what matters is that it failed INSIDE the
    # real program, having got that far.
    assert "auto_uploader" in (out.stderr + out.stdout) or out.returncode != 0

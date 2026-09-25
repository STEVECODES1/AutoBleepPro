"""bleep_engine imports the autoreel package, whose __init__ imports
autoreel.compliance - and compliance used to import bleep_engine back at
import time. A real upload attempt failed on it: "deadlock detected by
_ModuleLock('autoreel.compliance')", when the censor pass and the parallel
Rumble upload imported at the same moment. Checked in a fresh interpreter,
because a module already in sys.modules hides the cycle entirely.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh(code: str) -> str:
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_compliance_does_not_import_bleep_engine_at_import_time():
    out = _fresh("import sys, autoreel.compliance as c; "
                 "print('bleep_engine' in sys.modules, "
                 "c._BLEEP_ENGINE_AVAILABLE)")
    assert out == "False True"


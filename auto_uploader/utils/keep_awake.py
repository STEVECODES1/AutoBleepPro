"""
utils/keep_awake.py - hold Windows awake while real work is running, and
ONLY then.

The machine is meant to sleep between streams (SETUP-SLEEP.bat) and wake
on a timer to check for one. Windows puts a timer-woken machine back to
sleep about two minutes later unless a program says it is busy - this is
how a program says so. See:
https://learn.microsoft.com/windows/win32/power/system-wake-up-events

Held around work that must not be cut off: a video being censored and
uploaded, clips being cut, a clip being posted. Never around the idle
loops - setting the flag resets Windows' idle timer, so a hold taken on
every one-minute queue check would keep the machine awake forever.

Per thread, like the Windows call it wraps: hold it in the thread that
does the work.
"""

from __future__ import annotations

import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _set(flags: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        return False


class KeepAwake:
    """`with KeepAwake():` - the machine does not sleep inside the block.

    The display is left alone: keeping the screen lit is not needed to
    keep working, and is most of what makes a PC hot and loud overnight.
    """

    def __init__(self, why: str = "") -> None:
        self.why = why
        self.active = False

    def __enter__(self) -> "KeepAwake":
        self.active = _set(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        return self

    def __exit__(self, *exc) -> None:
        if self.active:
            # Back to normal power behaviour, or the machine would never
            # sleep again while this process lives.
            _set(ES_CONTINUOUS)
            self.active = False

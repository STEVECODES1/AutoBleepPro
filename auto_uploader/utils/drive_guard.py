"""Notice when the USB project drive (D:, Seagate Portable) dropped out and
came back, and recover without a person.

11-03 15:04 the drive reconnected as a new volume. Every process that was
already running kept its working folder on the OLD volume, so every
relative path failed with WinError 433 ("A device which does not exist was
specified") - for five hours every clip platform failed and tripped its
circuit breaker.

start(mode="exit"): the process exits (code 75) and its keepalive restarts it
on the live volume - for the uploader and the Rumble tools, whose open log
files are stale too. start(mode="chdir"): just re-enter the same folder by
its absolute path - for the recorder, which must not stop mid-stream (it
records to C:, only its working folder is on D:).
"""

import os
import threading
import time

# 433 device does not exist, 21 not ready, 1006 volume externally altered,
# 1167 device not connected, 6 invalid handle.
STALE = {433, 21, 1006, 1167, 6}
EXIT_CODE = 75


def _probe(folder: str) -> int:
    """0 when the folder answers through a RELATIVE path (what broke), else
    the Windows error number."""
    name = f".drive_probe_{os.getpid()}"
    try:
        with open(name, "w") as handle:
            handle.write("ok")
        os.remove(name)
        return 0
    except OSError as exc:
        code = getattr(exc, "winerror", None) or 0
        return code if code in STALE else 0


def _heal(folder: str, mode: str, say) -> None:
    try:
        os.chdir(folder)
    except OSError:
        return  # drive still gone - try again next minute
    if mode == "chdir":
        if _probe(folder) == 0:
            say(f"[Drive] The drive came back as a new volume - re-attached to {folder}.")
        return
    say(f"[Drive] The drive came back as a new volume - restarting this process "
        f"so every file opens on it again (exit {EXIT_CODE}, the keepalive restarts it).")
    time.sleep(2)
    os._exit(EXIT_CODE)


def start(mode: str = "exit", every_s: float = 60.0, say=print) -> threading.Thread:
    folder = os.path.abspath(os.getcwd())

    def loop():
        while True:
            time.sleep(every_s)
            try:
                if _probe(folder):
                    _heal(folder, mode, say)
            except Exception:
                pass

    thread = threading.Thread(target=loop, name="drive-guard", daemon=True)
    thread.start()
    return thread

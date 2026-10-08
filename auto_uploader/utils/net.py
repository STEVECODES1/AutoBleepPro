"""
utils/net.py - is the internet there yet?

The PC sleeps between streams and wakes every 20 minutes. For the first
few seconds after a wake the network is still coming back, and the queue
check that runs straight away posted into it: "Failed to resolve",
"upload rejected", a failure counted against the platform's breaker -
then the same clip went fine two minutes later (2026-10-08, every
platform, every few wakes).

So posting waits for this to say yes.
"""

from __future__ import annotations

import socket

_PROBES = (("www.google.com", 443), ("1.1.1.1", 443))


def online(timeout: float = 3.0) -> bool:
    """True when a name resolves and a connection opens. Never raises."""
    for host, port in _PROBES:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def wait_online(seconds: float = 45.0, step: float = 3.0) -> bool:
    """Wait up to `seconds` for the network. True once it is up."""
    import time

    deadline = time.time() + seconds
    while True:
        if online():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(step)

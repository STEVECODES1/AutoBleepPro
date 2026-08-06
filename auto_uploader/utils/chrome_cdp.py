"""
Makes sure a Chrome with remote debugging is actually there to attach to.

Rumble has no public API, so uploading drives a real logged-in browser.
Attaching to one you already opened is by far the best path: your session,
your cookies, no password stored anywhere, and 2FA is simply not this
tool's problem.

The trouble was what happened when nothing was listening on the port. It
fell straight through to launching a fresh Playwright browser and typing
your password into Rumble's login form - a different, worse path that
starts from a logged-out profile, has to guess selectors, and walks into
any captcha Rumble feels like showing. From the outside that looks like
"it opened some other browser instead", which is exactly what it did.

So: if nothing is on the port, start Chrome on it, pointed at a persistent
profile directory. The profile is the important part - log in once and
that directory keeps the session, so every later run attaches to a browser
that is already signed in and no password is ever needed.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from typing import Optional
from urllib.parse import urlparse

# Where Chrome usually lives on Windows. Checked in order; the first that
# exists wins.
WINDOWS_CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)

# Chrome refuses --remote-debugging-port on the default profile, so the
# launched instance gets its own directory. Persistent on purpose: it is
# what remembers the Rumble login between runs.
DEFAULT_PROFILE_DIR = r"C:\RumbleChromeProfile"

STARTUP_TIMEOUT_S = 30


def cdp_port(cdp_url: str) -> Optional[int]:
    """The port from a CDP URL, or None if it is not one.

    An explicit port is required. Defaulting to 80 for a bare hostname
    would turn a typo into "checked the web server on :80, found nothing,
    started a second Chrome" - a confusing failure instead of a clear one.
    """
    if not cdp_url:
        return None
    try:
        parsed = urlparse(cdp_url if "//" in cdp_url else f"http://{cdp_url}")
    except (ValueError, AttributeError):
        return None
    try:
        return parsed.port
    except ValueError:      # non-numeric port in the string
        return None


def is_listening(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Is something accepting connections on this port right now?

    A TCP connect rather than an HTTP request: it answers the only
    question that matters here in milliseconds, and cannot be confused by
    what the endpoint replies with.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_chrome() -> Optional[str]:
    """Chrome's executable, or None if it cannot be found."""
    for name in ("chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        for path in WINDOWS_CHROME_PATHS:
            if path and os.path.isfile(path):
                return path
    return None


def launch_args(chrome: str, port: int, profile_dir: str) -> list:
    return [
        chrome,
        f"--remote-debugging-port={port}",
        # Chrome will not enable remote debugging on the default profile,
        # so this is required rather than a nicety.
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        # Opened on the site we need, so logging in the first time is
        # obvious rather than a blank window with no explanation.
        "https://rumble.com/",
    ]


def ensure_chrome(cdp_url: str, profile_dir: str = "",
                  timeout: float = STARTUP_TIMEOUT_S) -> tuple:
    """(ready, message). Starts Chrome on the CDP port if nothing is there.

    Returns rather than raises: a failure here should fall back to
    whatever the caller wants to do, not end the upload.
    """
    port = cdp_port(cdp_url)
    if not port:
        return False, f"'{cdp_url}' is not a usable CDP URL"

    if is_listening(port):
        return True, f"Chrome already listening on {port}"

    chrome = find_chrome()
    if not chrome:
        return False, ("Chrome is not installed where this can find it - "
                       "start it yourself with "
                       f"--remote-debugging-port={port}")

    profile_dir = profile_dir or DEFAULT_PROFILE_DIR
    os.makedirs(profile_dir, exist_ok=True)

    try:
        subprocess.Popen(
            launch_args(chrome, port, profile_dir),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # Detached, so closing this console does not kill the browser
            # mid-upload.
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if sys.platform == "win32" else 0,
        )
    except OSError as exc:
        return False, f"could not start Chrome: {exc}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_listening(port):
            return True, (f"Started Chrome on port {port} using the profile at "
                          f"{profile_dir}. If Rumble asks you to log in, do it "
                          "once in that window - the profile remembers it.")
        time.sleep(0.5)

    return False, (f"Chrome was started but nothing is listening on {port} "
                   f"after {timeout:.0f}s")

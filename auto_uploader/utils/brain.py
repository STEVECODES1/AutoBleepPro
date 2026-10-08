"""The pipeline watching itself, so nobody has to sit and watch it.

WHY THIS EXISTS
---------------
Every piece of this project already handles its own failures - the
recorder never gives up, the uploader retries, the queue keeps clips that
cannot post yet. What nothing did was look at the WHOLE thing and notice
when it had quietly stopped working:

- The recorder window closed, froze or the PC slept. From the uploader's
  side that looks exactly like "no stream tonight": an empty folder.
- The stream is live and the recorder is sitting there "waiting".
- A recording is "running" but the file stopped growing.
- The disk is filling up and the next recording will die halfway.
- The uploader crashing on start, restarted by the keepalive forever.

And the only alerts were desktop pop-ups, which is to say no alerts at
all for anyone away from the desk.

WHAT IT DOES
------------
`Brain.tick()` runs every minute inside `--watch`. It works out the set
of problems right now and compares it with the last set:

  new problem   -> one URGENT alert (Discord @mention and/or ntfy.sh push)
  still there   -> reminded every `mute_hours`, never every minute
  gone          -> one quiet "fixed" message

Open problems are kept in logs/brain_state.json so a restart neither
re-sends them nor forgets them.

Where alerts go, all optional, from .env:
  DISCORD_ALERT_WEBHOOK_URL  (else DISCORD_JOB_WEBHOOK_URL, else
                              DISCORD_WEBHOOK_URL)
  DISCORD_ALERT_USER_ID      your numeric Discord id - urgent alerts
                             @mention it, which is what makes the phone buzz
  NTFY_TOPIC                 push to the free ntfy app (ntfy.sh/<topic>)
  NTFY_SERVER                self-hosted ntfy, default https://ntfy.sh

Every entry point swallows its own errors: the watchdog must never be
the thing that stops an upload.
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional, Tuple

from .job_report import webhook_url as _job_webhook_url
from .notifier import notify
from .self_healing import disk_free_gb

TICK_SECONDS = 60

RECORDER_STALE_MINUTES = 5
STALL_MINUTES = 5
LIVE_CHECK_MINUTES = 10
MUTE_HOURS = 6
CRASH_LOOP_STARTS = 5
CRASH_LOOP_MINUTES = 30

HEARTBEAT_DIR = ".heartbeat"     # matches tools/record_stream.py
_TIMEOUT = 15

# Problems whose detection needs minutes of history this process holds in
# memory. Straight after a restart that history is empty, which is not
# the same as the problem being fixed.
_NEEDS_HISTORY = ("stalled", "live_not_recording")

Problems = Dict[str, Tuple[str, str]]


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _post(url: str, body: bytes, headers: dict) -> bool:
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        urllib.request.urlopen(request, timeout=_TIMEOUT)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError):
        return False


def _header(text: str) -> str:
    # HTTP headers are latin-1; a title with an emoji in it would raise.
    return text.encode("ascii", "replace").decode("ascii")[:200]


def alert_channels() -> list:
    channels = []
    if _env("DISCORD_ALERT_WEBHOOK_URL") or _job_webhook_url():
        channels.append("Discord" + (" @mention" if _env(
            "DISCORD_ALERT_USER_ID").isdigit() else ""))
    if _env("NTFY_TOPIC"):
        channels.append("ntfy")
    return channels


def send_alert(title: str, message: str, urgent: bool = True,
               post: Optional[Callable] = None) -> list:
    """Send to every configured channel. Returns the ones that took it."""
    post = post or _post
    reached = []

    url = _env("DISCORD_ALERT_WEBHOOK_URL") or _job_webhook_url()
    if url:
        user = _env("DISCORD_ALERT_USER_ID")
        mention = f"<@{user}> " if urgent and user.isdigit() else ""
        payload = {
            "username": "AutoBleep brain",
            "content": f"{mention}**{title}**\n{message}"[:2000],
            # Only the operator is ever pinged - never @everyone from a
            # log line that happened to contain it.
            "allowed_mentions": ({"users": [user]} if mention
                                 else {"parse": []}),
        }
        try:
            if post(url, json.dumps(payload).encode("utf-8"),
                    {"Content-Type": "application/json",
                     "User-Agent": "AutoBleep"}):
                reached.append("discord")
        except Exception:
            pass

    topic = _env("NTFY_TOPIC")
    if topic:
        server = (_env("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
        headers = {"Title": _header(title),
                   "Priority": "high" if urgent else "default",
                   "Tags": "rotating_light" if urgent else "white_check_mark"}
        try:
            if post(f"{server}/{topic}", message.encode("utf-8"), headers):
                reached.append("ntfy")
        except Exception:
            pass
    return reached


def _default_live_check(project_root: str) -> Optional[Callable]:
    """record_stream.channel_is_live, if the recorder sits beside us."""
    tools = os.path.join(os.path.dirname(os.path.abspath(project_root)),
                         "tools")
    if not os.path.isfile(os.path.join(tools, "record_stream.py")):
        return None
    try:
        if tools not in sys.path:
            sys.path.insert(0, tools)
        from record_stream import channel_is_live
        return channel_is_live
    except Exception:
        return None


def _minutes(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 \
        else f"{minutes} min"


class Brain:
    def __init__(self, cfg, settings: Optional[dict] = None, *,
                 send: Optional[Callable] = None,
                 live_check: Optional[Callable] = None,
                 desktop: bool = False,
                 now: Callable[[], float] = time.time,
                 say: Callable = print):
        features = getattr(cfg, "features", None) or {}
        s = settings if settings is not None else (features.get("brain") or {})
        heal = features.get("self_healing") or {}
        self.enabled = bool(s.get("enabled", True))
        self.stale_s = float(s.get("recorder_stale_minutes",
                                   RECORDER_STALE_MINUTES)) * 60
        self.stall_s = float(s.get("stall_minutes", STALL_MINUTES)) * 60
        self.live_check_s = float(s.get("live_check_minutes",
                                        LIVE_CHECK_MINUTES)) * 60
        self.mute_s = float(s.get("mute_hours", MUTE_HOURS)) * 3600
        self.min_free_gb = float(s.get("min_free_gb",
                                       heal.get("min_free_gb", 10)))

        root = cfg.project_root
        staging = s.get("recorder_staging") or "./recording"
        staging = staging if os.path.isabs(staging) \
            else os.path.join(root, staging)
        self.heartbeat_dir = os.path.join(staging, HEARTBEAT_DIR)
        self.watch_folder = cfg.general.watch_folder
        self.state_path = os.path.join(cfg.general.logs_folder,
                                       "brain_state.json")

        self._send = send or send_alert
        self._live_check = (live_check if live_check is not None
                            else _default_live_check(root))
        self._desktop = desktop
        self._now = now
        self._say = say
        self.started = now()
        self._bytes: Dict[str, Tuple[int, float]] = {}
        self._live_seen: Dict[str, float] = {}
        self._next_live_check = 0.0
        self.state = self._load()

    # ── state ────────────────────────────────────────────────────────────
    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                state = json.load(handle)
            if isinstance(state, dict):
                state.setdefault("starts", [])
                state.setdefault("open", {})
                return state
        except (OSError, ValueError):
            pass
        return {"starts": [], "open": {}}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            temp = self.state_path + ".tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=1)
            os.replace(temp, self.state_path)
        except OSError:
            pass

    # ── what is wrong right now ──────────────────────────────────────────
    def on_start(self) -> None:
        """Called once when --watch starts. Restarts are how a crash loop
        shows itself: the keepalive brings it back forever, by design."""
        now = self._now()
        window = CRASH_LOOP_MINUTES * 60
        self.state["starts"] = [t for t in self.state["starts"]
                                if now - t < window] + [now]
        self._save()

    def _crash_loop(self, now: float) -> Problems:
        recent = [t for t in self.state["starts"]
                  if now - t < CRASH_LOOP_MINUTES * 60]
        if len(recent) >= CRASH_LOOP_STARTS:
            return {"crash_loop": (
                "Uploader keeps crashing",
                f"It has restarted {len(recent)} times in the last "
                f"{CRASH_LOOP_MINUTES} min. Read the uploader window for "
                f"the error it prints before each restart.")}
        return {}

    def _disk(self) -> Problems:
        free = disk_free_gb(self.watch_folder)
        if 0 <= free < self.min_free_gb:
            return {"low_disk": (
                "Disk almost full",
                f"{free:.1f} GB free where recordings go (want at least "
                f"{self.min_free_gb:.0f} GB). The next long stream may stop "
                f"halfway. Delete old files from uploaded/ or censored/.")}
        return {}

    def _beats(self) -> list:
        beats = []
        for path in glob.glob(os.path.join(self.heartbeat_dir, "*.json")):
            try:
                with open(path, encoding="utf-8") as handle:
                    beat = json.load(handle)
                if isinstance(beat, dict) and beat.get("time"):
                    beats.append(beat)
            except (OSError, ValueError):
                continue
        return beats

    def _recorder(self, now: float) -> Problems:
        beats = self._beats()
        if not beats:
            # Never seen a heartbeat: a recorder from before heartbeats,
            # or none at all. Nothing to compare against.
            return {}
        newest = max(float(b["time"]) for b in beats)
        if now - newest > self.stale_s:
            self._bytes.clear()
            self._live_seen.clear()
            return {"recorder_down": (
                "Recorder is not running",
                f"No sign of life for {_minutes(now - newest)} - its window "
                f"was closed, it froze, or the PC went to sleep. Streams are "
                f"NOT being recorded. Run START.bat.")}

        problems: Problems = {}
        check_live = (self._live_check is not None
                      and now >= self._next_live_check)
        if check_live:
            self._next_live_check = now + self.live_check_s
        for beat in beats:
            name = str(beat.get("name") or "stream")
            if beat.get("state") == "recording":
                self._live_seen.pop(name, None)
                size = int(beat.get("bytes") or 0)
                last = self._bytes.get(name)
                if last is None or size != last[0]:
                    self._bytes[name] = (size, now)
                elif now - last[1] >= self.stall_s:
                    problems[f"stalled:{name}"] = (
                        f"{name} recording is stuck",
                        f"The file has not grown for {_minutes(now - last[1])}"
                        f" ({size / 1e9:.2f} GB so far). The recorder "
                        f"normally recovers by itself - if this does not "
                        f"clear in a few minutes, close the recorder window "
                        f"and run START.bat.")
                continue

            self._bytes.pop(name, None)
            if check_live:
                try:
                    live = self._live_check(str(beat.get("url") or ""))
                except Exception:
                    live = None
                if live:
                    self._live_seen.setdefault(name, now)
                elif live is False:
                    self._live_seen.pop(name, None)
            seen = self._live_seen.get(name)
            # Live on two checks a full interval apart while the recorder
            # still says "waiting": a stream start takes seconds, not ten
            # minutes.
            if seen is not None and now - seen >= self.live_check_s:
                problems[f"live_not_recording:{name}"] = (
                    f"{name} is LIVE but not being recorded",
                    f"The channel has been live for at least "
                    f"{_minutes(now - seen)} and the recorder is still "
                    f"waiting. Check the recorder window for an error; "
                    f"restarting it (START.bat) usually fixes it.")
        return problems

    def problems(self) -> Problems:
        now = self._now()
        found: Problems = {}
        for check in (lambda: self._crash_loop(now), self._disk,
                      lambda: self._recorder(now)):
            try:
                found.update(check())
            except Exception as exc:
                self._say(f"[Brain] WARNING: a check failed: {exc}")
        return found

    # ── acting on it ─────────────────────────────────────────────────────
    def _alert(self, title: str, message: str, urgent: bool) -> None:
        reached = []
        try:
            reached = self._send(title, message, urgent=urgent)
        except Exception:
            pass
        if self._desktop:
            notify(title, message, True)
        where = ", ".join(reached) if reached else \
            "nowhere - set NTFY_TOPIC or DISCORD_ALERT_USER_ID in .env"
        self._say(f"[Brain] {'ALERT' if urgent else 'OK'}: {title} - "
                  f"{message} (sent to {where})")

    def tick(self) -> Problems:
        if not self.enabled:
            return {}
        now = self._now()
        current = self.problems()
        opened = self.state["open"]
        changed = False

        for key, (title, message) in current.items():
            entry = opened.get(key)
            if entry is None:
                self._alert(title, message, urgent=True)
                opened[key] = {"title": title, "since": now, "sent": now}
                changed = True
            elif now - float(entry.get("sent", 0)) >= self.mute_s:
                self._alert(f"Still: {title}", message, urgent=True)
                entry["sent"] = now
                changed = True

        warmed_up = now - self.started >= max(self.stall_s,
                                              self.live_check_s) + 60
        for key in list(opened):
            if key in current:
                continue
            if key.split(":")[0] in _NEEDS_HISTORY and not warmed_up:
                continue
            entry = opened.pop(key)
            lasted = _minutes(now - float(entry.get("since", now)))
            self._alert(f"Fixed: {entry.get('title', key)}",
                        f"Cleared after {lasted}.", urgent=False)
            changed = True

        if changed:
            self._save()
        return current

    def describe(self) -> str:
        channels = alert_channels()
        where = ", ".join(channels) if channels else \
            "console only (set NTFY_TOPIC or DISCORD_ALERT_USER_ID in .env)"
        return (f"[Brain] Watching the recorder, disk space and crash loops "
                f"every minute. Alerts: {where}.")

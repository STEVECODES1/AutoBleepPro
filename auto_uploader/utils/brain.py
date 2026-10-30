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

CHROME_CRASHES_15MIN = 5
CHROME_DOWN_MINUTES = 5
STUCK_STREAM_HOURS = 6
STUCK_CLIP_HOURS = 2
BIG_VIDEO_BYTES = 500 * 1024 * 1024
DIGEST_HOUR = 10
# The four channels we run, and the token that can read each one. The
# guard is a read (1 unit), once a day: if YouTube strips a "(Fan
# Channel)" name or the About notice again, the next tick says so
# instead of waiting for another removal.
CHANNEL_GUARD_HOURS = 24
CHANNEL_GUARD_TOKENS = (
    ("wopovod", "youtube_token.json"),
    ("clips", "youtube_shorts_token.json"),
    ("games", "youtube_games_token.json"),
    ("compilations", "youtube_compilation_token.json"),
)
_VIDEO_EXTS = (".ts", ".mp4", ".mkv", ".flv", ".mov", ".webm")

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


def _default_chrome_check(cfg) -> Optional[Callable]:
    """'Is the Rumble Chrome answering?' when a CDP port is configured."""
    url = str(getattr(getattr(cfg, "rumble", None), "cdp_url", "") or "")
    if not url:
        return None
    try:
        from .chrome_cdp import cdp_port, is_listening
    except Exception:
        return None
    port = cdp_port(url)
    if not port:
        return None
    return lambda: is_listening(port)


def _channel_service(secrets_path: str, token_path: str):
    """A read-only YouTube service that never opens a browser.

    YouTubeUploader is the normal route, but on a dead refresh token it
    falls through to an interactive OAuth flow - and a watchdog with
    nobody at the keyboard must not sit there. This refreshes what it
    can and raises a short, reportable error otherwise.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(token_path)
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            raise RuntimeError("sign-in expired")
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_problems(cfg) -> Problems:
    """The four channel names/Abouts, as problems. Empty when all good.

    Shared by the watchdog (once a day) and tools/channel_guard.py (a
    scheduled task, so the check still runs when the uploader is down).
    """
    youtube = getattr(cfg, "youtube", None)
    secrets_path = str(getattr(youtube, "client_secrets_path", "") or "")
    if not secrets_path or not os.path.isfile(secrets_path):
        return {}
    root = getattr(cfg, "project_root", "")
    from .disclosure import ACTORS_LINE, FAN_LINE

    found: Problems = {}
    for name, token in CHANNEL_GUARD_TOKENS:
        token_path = os.path.join(root, token)
        try:
            yt = _channel_service(secrets_path, token_path)
            items = yt.channels().list(
                part="snippet,brandingSettings", mine=True
            ).execute().get("items", [])
            if not items:
                raise RuntimeError("no channel returned")
            snippet = items[0].get("snippet", {}) or {}
            about = (items[0].get("brandingSettings", {}).get("channel", {})
                     or {}).get("description", "") or ""
            title = str(snippet.get("title", "") or "")
            bad = []
            if "fan" not in title.lower():
                bad.append(f"the name is now {title!r} (no 'fan')")
            if FAN_LINE not in about or ACTORS_LINE not in about:
                bad.append("the About lost the fan/actors notice")
            if bad:
                found[f"channel_guard:{name}"] = (
                    f"{name} no longer says it is a fan channel",
                    "YouTube's impersonation rules need a fan channel to "
                    "say so in its name/handle and in its About. Found: "
                    + "; ".join(bad) + ". Put it back in Studio "
                    "(Customize channel) or run tools/funnel_setup.py.")
        except Exception as exc:
            found[f"channel_guard:{name}"] = (
                f"Could not check {name}",
                f"The daily fan-label check failed: {exc}. If this says "
                f"sign-in expired, run `python main.py --setup-youtube` "
                f"or --setup-shorts for that channel. Otherwise it will "
                f"try again in an hour.")
    return found


def _default_channel_check(cfg) -> Optional[Callable]:
    """The daily watchdog check, or None when no YouTube credentials.

    None when this machine has no YouTube credentials configured, so a
    unit test (and a fresh install) never touches the network.
    """
    youtube = getattr(cfg, "youtube", None)
    secrets_path = str(getattr(youtube, "client_secrets_path", "") or "")
    if not secrets_path or not os.path.isfile(secrets_path):
        return None
    return lambda: channel_problems(cfg)


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _minutes(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 \
        else f"{minutes} min"


class Brain:
    def __init__(self, cfg, settings: Optional[dict] = None, *,
                 send: Optional[Callable] = None,
                 live_check: Optional[Callable] = None,
                 chrome_check: Optional[Callable] = None,
                 channel_check: Optional[Callable] = None,
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
        # The same folder the recorder writes to (general.recording_folder,
        # which tools/record_stream.py also reads), unless overridden here.
        staging = (s.get("recorder_staging")
                   or getattr(getattr(cfg, "general", None), "recording_folder", "")
                   or "./recording")
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
        self.project_root = root
        self.logs_folder = cfg.general.logs_folder
        # The Rumble Chrome (uploads + live relay) - only watched when one
        # is configured. Injectable so tests never probe a real port.
        self._chrome_check = chrome_check if chrome_check is not None \
            else _default_chrome_check(cfg)
        self._chrome_down_since: Optional[float] = None
        # Once a day, four read-only calls: did YouTube strip a fan label
        # or the About notice? Injectable so tests never touch the API.
        self._channel_check = channel_check if channel_check is not None \
            else _default_channel_check(cfg)
        self._channel_guard_at = 0.0
        self._channel_guard_problems: Problems = {}
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

    def _chrome(self, now: float) -> Problems:
        """The Rumble Chrome closed and nobody brought it back."""
        if self._chrome_check is None:
            return {}
        try:
            up = bool(self._chrome_check())
        except Exception:
            return {}
        if up:
            self._chrome_down_since = None
            return {}
        if self._chrome_down_since is None:
            self._chrome_down_since = now
        down = now - self._chrome_down_since
        if down < CHROME_DOWN_MINUTES * 60:
            return {}
        return {"chrome_down": (
            "Rumble Chrome is closed",
            f"The Chrome window that Rumble uploads and the live relay use "
            f"has not answered for {_minutes(down)}. Nothing can go to Rumble "
            f"until it is back. Its keeper reopens it within a minute - if "
            f"this does not clear, run START.bat.")}

    def _chrome_crashing(self, now: float) -> Problems:
        """10-30: the AutoBleep Chrome's profile cache went bad and every
        page crashed ("Aw, Snap!" STATUS_ACCESS_VIOLATION) about once a
        minute - Rumble could not upload. Crash dumps pile up in the
        profile's Crashpad folder; many in a short time = that."""
        folder = os.environ.get("AUTOBLEEP_CHROME_CRASHES",
                                r"C:\RumbleChromeProfile\Crashpad\reports")
        try:
            recent = [f for f in os.listdir(folder)
                      if now - os.path.getmtime(os.path.join(folder, f)) < 15 * 60]
        except OSError:
            return {}
        if len(recent) < CHROME_CRASHES_15MIN:
            return {}
        return {"chrome_crashing": (
            "AutoBleep Chrome keeps crashing",
            f"{len(recent)} crashes in 15 min - its cache is probably corrupt. Close that "
            f"Chrome window, then run tools\\fix_chrome_cache.ps1 (moves the caches to "
            f"_to_delete; logins stay). Rumble uploads fail until then.")}

    def _channel_guard(self, now: float) -> Problems:
        """A daily look at the four channel names and Abouts.

        The 10-18 removal happened with nothing in this process able to
        notice: every local piece was healthy, and YouTube had already
        renamed or removed the channel. Four reads a day is 4 quota
        units against 10,000, and it catches the next one before a
        removal rather than after.
        """
        if self._channel_check is None:
            return {}
        if now - self._channel_guard_at < CHANNEL_GUARD_HOURS * 3600:
            return self._channel_guard_problems
        self._channel_guard_at = now
        try:
            self._channel_guard_problems = dict(self._channel_check() or {})
        except Exception as exc:
            self._say(f"[Brain] WARNING: channel check failed: {exc}")
            # A transient network error is not a reason to wait a full
            # day; the next tick tries again in an hour.
            self._channel_guard_at = now - CHANNEL_GUARD_HOURS * 3600 + 3600
            self._channel_guard_problems = {}
        return self._channel_guard_problems

    def _youtube_blocked(self, now: float) -> Problems:
        """wopovod refused an upload (a strike or an upload limit)."""
        data = _read_json(os.path.join(self.logs_folder, "youtube_blocked.json"), {})
        try:
            until = float((data or {}).get("until", 0))
        except (TypeError, ValueError, AttributeError):
            return {}
        if until <= now:
            return {}
        return {"youtube_blocked": (
            "YouTube is refusing uploads",
            f"wopovod refused an upload - usually a strike or a daily upload "
            f"limit. Full streams wait until "
            f"{time.strftime('%H:%M', time.localtime(until))} and then go up "
            f"by themselves. Check YouTube Studio for a notice.")}

    def _upload_moving(self, now: float) -> bool:
        """An upload touched logs/uploading.json in the last 10 minutes."""
        try:
            return now - os.path.getmtime(
                os.path.join(self.logs_folder, "uploading.json")) < 600
        except OSError:
            return False

    def _stuck_streams(self, now: float) -> Problems:
        """A full stream sitting in the watch folder with nothing moving.

        10-17: the money stream's YouTube upload was cut off and nothing
        said so for hours. A big video still here after hours, with no
        upload under way, is waiting on something a person has to fix."""
        try:
            names = os.listdir(self.watch_folder)
        except OSError:
            return {}
        if self._upload_moving(now):
            return {}
        problems: Problems = {}
        for name in names:
            if not name.lower().endswith(_VIDEO_EXTS):
                continue
            path = os.path.join(self.watch_folder, name)
            try:
                size, mtime = os.path.getsize(path), os.path.getmtime(path)
            except OSError:
                continue
            age = now - mtime
            if size >= BIG_VIDEO_BYTES and age >= STUCK_STREAM_HOURS * 3600:
                problems[f"stuck_stream:{name}"] = (
                    "A full stream is stuck",
                    f"{name} ({size / 1e9:.1f} GB) has waited "
                    f"{_minutes(age)} in watch_folder and nothing is uploading "
                    f"it. Usually a platform refused it - the uploader window "
                    f"says which. It retries by itself; a restart "
                    f"(START.bat) forces a fresh try.")
        return problems

    def _stuck_clips(self, now: float) -> Problems:
        """A clip post that has said 'in progress' for hours."""
        data = _read_json(os.path.join(self.project_root, "clip_jobs.json"), {})
        jobs = data.get("jobs", {}) if isinstance(data, dict) else {}
        jobs = jobs.values() if isinstance(jobs, dict) else jobs
        stuck = []
        for job in jobs:
            if not isinstance(job, dict) or job.get("state") != "in_progress":
                continue
            since = float(job.get("claimed_at") or job.get("updated_at") or now)
            if now - since >= STUCK_CLIP_HOURS * 3600:
                stuck.append(job)
        if not stuck:
            return {}
        first = stuck[0]
        return {"stuck_clip": (
            "A clip post is stuck",
            f"{len(stuck)} clip post(s) have said 'in progress' for over "
            f"{STUCK_CLIP_HOURS} h (e.g. {first.get('platform')}: "
            f"{os.path.basename(str(first.get('clip_path', '')))}). Restarting "
            f"the uploader (START.bat) frees them.")}

    def problems(self) -> Problems:
        now = self._now()
        found: Problems = {}
        for check in (lambda: self._crash_loop(now), self._disk,
                      lambda: self._recorder(now), lambda: self._chrome(now),
                      lambda: self._chrome_crashing(now),
                      lambda: self._youtube_blocked(now),
                      lambda: self._channel_guard(now),
                      lambda: self._stuck_streams(now),
                      lambda: self._stuck_clips(now)):
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

    # ── once a day: what the last 24 hours did ──────────────────────────
    _NAMES = {"youtube_shorts": "Shorts", "instagram": "IG", "facebook": "FB",
              "buffer_x": "X", "buffer_tiktok": "TikTok", "rumble": "Rumble"}

    def digest_text(self, now: Optional[float] = None) -> str:
        """The owner away from the PC reads this and knows where things are."""
        now = self._now() if now is None else now
        day = 86400
        lines = []
        links = _read_json(os.path.join(self.logs_folder, "stream_links.json"), [])
        streams = [e for e in links if isinstance(e, dict)
                   and now - float(e.get("at", 0) or 0) < day]
        if streams:
            lines.append("**Full streams**")
            for e in streams[-5:]:
                where = " · ".join(f"{name} {e[key]}" for key, name in
                                   (("youtube", "YouTube"), ("rumble", "Rumble"))
                                   if str(e.get(key, "")).startswith("https://"))
                lines.append(f"• {e.get('title', '?')} ({e.get('date', '')}) - {where or 'no links yet'}")
        else:
            lines.append("No full stream went up in the last 24 h.")
        data = _read_json(os.path.join(self.project_root, "clip_jobs.json"), {})
        jobs = data.get("jobs", {}) if isinstance(data, dict) else {}
        jobs = [j for j in (jobs.values() if isinstance(jobs, dict) else jobs)
                if isinstance(j, dict)
                and now - float(j.get("updated_at", 0) or 0) < day]
        posted: Dict[str, int] = {}
        held = failed = waiting = 0
        for job in jobs:
            state, error = job.get("state"), str(job.get("last_error") or "")
            if state == "done":
                name = self._NAMES.get(job.get("platform"), job.get("platform"))
                posted[name] = posted.get(name, 0) + 1
            elif state == "failed" and error.startswith("held"):
                held += 1
            elif state == "failed":
                failed += 1
            elif state == "blocked":
                waiting += 1
        if posted:
            lines.append("**Clips posted:** " + ", ".join(
                f"{name} {count}" for name, count in sorted(posted.items())))
        else:
            lines.append("**Clips posted:** none")
        lines.append(f"Held back by the guards: {held} · waiting for a slot: "
                     f"{waiting} · failed: {failed}")
        free = disk_free_gb(self.watch_folder)
        if free >= 0:
            lines.append(f"Disk: {free:.0f} GB free")
        opened = self.state.get("open") or {}
        if opened:
            lines.append("**Needs you:** " + "; ".join(
                str(v.get("title", k)) for k, v in opened.items()))
        else:
            lines.append("Nothing needs you right now.")
        return "\n".join(lines)[:1900]

    def daily_digest(self, hour: int = DIGEST_HOUR) -> bool:
        """Once a day, after `hour` local time. Never urgent."""
        if not self.enabled:
            return False
        now = self._now()
        local = time.localtime(now)
        today = time.strftime("%Y-%m-%d", local)
        if local.tm_hour < hour or self.state.get("digest_day") == today:
            return False
        self.state["digest_day"] = today
        self._save()
        self._alert("Daily report", self.digest_text(now), urgent=False)
        return True

    def describe(self) -> str:
        channels = alert_channels()
        where = ", ".join(channels) if channels else \
            "console only (set NTFY_TOPIC or DISCORD_ALERT_USER_ID in .env)"
        return (f"[Brain] Watching the recorder, the Rumble Chrome, YouTube "
                f"refusals, stuck streams and clip posts, disk space and crash "
                f"loops every minute; a daily report after {DIGEST_HOUR}:00. "
                f"Alerts: {where}.")

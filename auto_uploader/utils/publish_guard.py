"""
The only component allowed to authorise a post.

Every publisher asks `PublishGuard.check(platform)` first and does nothing
unless it comes back allowed. That is the whole design: one place to say
no, so a bug in a publisher cannot become a posting spree.

WHY THE DEFAULTS ARE LOW
------------------------
The caps here sit well under what the APIs permit - 5 Instagram posts a day
against a documented ceiling of 25. The binding constraint is not the rate
limit, it's how the account looks to a spam classifier, and that threshold
is invisible and unappealable. Room to raise the caps exists; starting
there does not.

WHAT THIS DELIBERATELY WILL NOT DO
----------------------------------
There is no mechanism to route around a cap - no proxy support, no
alternate credentials, no "just one more". Evading a documented limit turns
a recoverable "you posted too much" into an unrecoverable "this account
evaded enforcement".
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

# Platforms that are never authorised automatically, whatever config says.
# Reddit: sitewide anti-spam plus per-subreddit filters are the real limit,
#         not an API quota, and they are enforced by ban.
# Facebook groups: group publishing permissions were withdrawn from the
#         Graph API, so there is no approved automated route at all.
ALWAYS_MANUAL: frozenset = frozenset({"reddit", "facebook_group"})

# Rolling window, not calendar day: Meta enforces "per 24 hours" from the
# time of each post, so a midnight reset would allow a double burst.
WINDOW_SECONDS = 24 * 60 * 60

DEFAULT_KILL_SWITCH_FILE = "STOP_POSTING"


@dataclass
class Decision:
    """Whether a post may proceed, and why not if it may not."""
    allowed: bool
    reason: str
    retry_after_s: Optional[float] = None   # None = not a timing problem

    def __bool__(self) -> bool:
        return self.allowed


@dataclass
class PublishGuard:
    """Rate limits, kill switch, and circuit breaker for outbound posts."""

    config: dict = field(default_factory=dict)
    state_path: str = "./posting_state.json"
    _state: dict = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._load()

    # ── State ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self._state = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            self._state = {}
        self._state.setdefault("posts", {})       # platform -> [timestamps]
        self._state.setdefault("failures", {})    # platform -> consecutive count

    def _save(self) -> None:
        """Atomic write - a crash mid-save must not lose the post history,
        because losing it resets the caps and permits a burst."""
        directory = os.path.dirname(os.path.abspath(self.state_path))
        os.makedirs(directory, exist_ok=True)
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
            os.replace(tmp, self.state_path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ── Config accessors ─────────────────────────────────────────────────

    def _platform_config(self, platform: str) -> dict:
        platforms = (self.config.get("platforms") or {})
        return platforms.get(platform) or {}

    def is_manual_only(self, platform: str) -> bool:
        if platform in ALWAYS_MANUAL:
            return True
        return bool(self._platform_config(platform).get("manual_approval_only", False))

    def kill_switch_engaged(self) -> tuple[bool, str]:
        """(engaged, reason).

        Two forms on purpose. The config flag is the deliberate one; the
        file is the panic one - it stops a long-running --watch without
        editing JSON or hunting for the process, and it works even if the
        config is mid-edit.
        """
        if not self.config.get("enabled", False):
            return True, "posting.enabled is false"
        path = self.config.get("kill_switch_file") or DEFAULT_KILL_SWITCH_FILE
        if os.path.exists(path):
            return True, f"kill switch file present: {path}"
        return False, ""

    # ── Rolling window ───────────────────────────────────────────────────

    def _recent_posts(self, platform: str, now: Optional[float] = None) -> list:
        now = time.time() if now is None else now
        stamps = self._state["posts"].get(platform) or []
        fresh = [t for t in stamps if isinstance(t, (int, float)) and now - t < WINDOW_SECONDS]
        if len(fresh) != len(stamps):
            self._state["posts"][platform] = fresh
        return sorted(fresh)

    def posts_in_window(self, platform: str, now: Optional[float] = None) -> int:
        return len(self._recent_posts(platform, now))

    def consecutive_failures(self, platform: str) -> int:
        return int(self._state["failures"].get(platform, 0) or 0)

    # ── The decision ─────────────────────────────────────────────────────

    def check(self, platform: str, now: Optional[float] = None) -> Decision:
        """May `platform` be posted to right now?

        Checked cheapest-and-most-absolute first, so a kill switch beats
        everything and no amount of per-platform config can override it.
        """
        now = time.time() if now is None else now

        engaged, why = self.kill_switch_engaged()
        if engaged:
            return Decision(False, f"KILL SWITCH: {why}")

        if self.is_manual_only(platform):
            return Decision(
                False,
                f"{platform} is manual-approval only - queued for a human, "
                "never auto-posted")

        settings = self._platform_config(platform)
        if not settings:
            return Decision(False, f"{platform} is not configured under posting.platforms")
        if not settings.get("enabled", False):
            return Decision(False, f"{platform} is disabled in config")

        breaker = int((self.config.get("circuit_breaker") or {}).get(
            "consecutive_failures", 3))
        failures = self.consecutive_failures(platform)
        if breaker > 0 and failures >= breaker:
            return Decision(
                False,
                f"circuit breaker open for {platform}: {failures} consecutive "
                "failures. Something is wrong at the account or credential "
                "level - fix it, then clear with reset_failures()")

        recent = self._recent_posts(platform, now)
        cap = int(settings.get("daily_cap", 0) or 0)
        if cap > 0 and len(recent) >= cap:
            oldest = recent[0]
            wait = max(0.0, (oldest + WINDOW_SECONDS) - now)
            return Decision(
                False,
                f"{platform} daily cap reached ({len(recent)}/{cap} in the last 24h)",
                retry_after_s=wait)

        spacing_min = float(settings.get("min_minutes_between", 0) or 0)
        if spacing_min > 0 and recent:
            since = now - recent[-1]
            needed = spacing_min * 60
            if since < needed:
                return Decision(
                    False,
                    f"{platform} posted {since / 60:.0f} min ago; minimum "
                    f"spacing is {spacing_min:.0f} min",
                    retry_after_s=needed - since)

        remaining = "unlimited" if cap <= 0 else f"{cap - len(recent)} left today"
        return Decision(True, f"{platform} OK ({remaining})")

    # ── Recording outcomes ───────────────────────────────────────────────

    def record_post(self, platform: str, now: Optional[float] = None) -> None:
        """Call immediately AFTER a successful post.

        Recorded even though the caller could crash next - an unrecorded
        post is one the cap doesn't know about, which is the failure mode
        that permits a burst.
        """
        now = time.time() if now is None else now
        self._state["posts"].setdefault(platform, []).append(now)
        self._state["failures"][platform] = 0    # success clears the breaker
        self._save()

    def record_failure(self, platform: str) -> int:
        """Call after a failed post. Returns the new consecutive count."""
        count = self.consecutive_failures(platform) + 1
        self._state["failures"][platform] = count
        self._save()
        return count

    def reset_failures(self, platform: Optional[str] = None) -> None:
        """Clear the circuit breaker, deliberately and manually."""
        if platform is None:
            self._state["failures"] = {}
        else:
            self._state["failures"].pop(platform, None)
        self._save()

    # ── Reporting ────────────────────────────────────────────────────────

    def status(self, platforms: Optional[Iterable[str]] = None) -> list:
        """(platform, allowed, reason) for each platform, for `--posting-status`."""
        names = list(platforms) if platforms else sorted(
            (self.config.get("platforms") or {}).keys())
        out = []
        for name in names:
            decision = self.check(name)
            out.append((name, decision.allowed, decision.reason))
        return out


def engage_kill_switch(config: dict, note: str = "") -> str:
    """Create the kill-switch file. Returns the path written."""
    path = config.get("kill_switch_file") or DEFAULT_KILL_SWITCH_FILE
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"Posting halted {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        if note:
            f.write(note + "\n")
        f.write("Delete this file to allow posting again.\n")
    return path


def release_kill_switch(config: dict) -> bool:
    """Remove the kill-switch file. True if one was there."""
    path = config.get("kill_switch_file") or DEFAULT_KILL_SWITCH_FILE
    try:
        os.remove(path)
        return True
    except OSError:
        return False

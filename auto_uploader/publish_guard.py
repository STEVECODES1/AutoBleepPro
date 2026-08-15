"""
The only component allowed to authorise a post.

Every publisher asks the guard first and does nothing unless it comes
back allowed. That is the whole design: one place to say no, so a bug in
a publisher cannot become a posting spree.

WHY THE DEFAULTS ARE LOW
------------------------
The caps here sit well under what the APIs permit - 5 Instagram posts a
day against a documented ceiling of 25. The binding constraint is not the
rate limit, it's how the account looks to a spam classifier, and that
threshold is invisible and unappealable. Room to raise the caps exists;
starting there does not.

WHAT THIS DELIBERATELY WILL NOT DO
----------------------------------
There is no mechanism to route around a cap - no proxy support, no
alternate credentials to dodge a limit, no "just one more". Evading a
documented limit turns a recoverable "you posted too much" into an
unrecoverable "this account evaded enforcement".

TWO CALLING CONVENTIONS
-----------------------
`check()` returns a Decision and takes an injectable `now`, which is what
the tests and the queue use. `can_post()`/`record_result()` are the
(allowed, reason) pair the publishers in publishers/ were written
against. Both run the identical checks - there is one implementation, on
purpose, because two guards means one of them is the hole.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional

# Platforms with no approved automated route at all, so config must not be
# able to enable them.
#
# Facebook groups qualify because group publishing permissions were
# withdrawn from the Graph API - there is no compliant way to automate it,
# and that is a property of the platform, not of any one account. A config
# flag cannot turn this off; that is the point of the list.
#
# Reddit is NOT here. Reddit has a supported API (PRAW) and a documented
# way to post; the risk is per-account reputation, not the absence of a
# route. That makes it a config decision - `manual_approval_only` - which
# ships true and can be turned off once a healthy account is configured.
# It stays behind the same caps, spacing and circuit breaker either way.
ALWAYS_MANUAL: frozenset = frozenset({"facebook_group"})

# Rolling window, not calendar day: Meta enforces "per 24 hours" from the
# time of each post, so a midnight reset would allow a double burst.
WINDOW_SECONDS = 24 * 60 * 60

DEFAULT_KILL_SWITCH_FILE = "STOP_POSTING"

# The cap key. "max_per_day" is here because a platform block was once
# written with that name, nothing read it, and the guard reported
# "unlimited" for a platform whose config plainly said 3 - a typo in a
# key is invisible, and reads as "no limit" precisely where a limit was
# meant.
CAP_KEYS = ("daily_cap", "max_per_day")

# Everything a platform block may contain. Anything else is a typo, and
# saying so is the only way a silently-ignored limit gets noticed.
KNOWN_PLATFORM_KEYS = frozenset({
    "enabled", "daily_cap", "max_per_day", "min_minutes_between",
    "manual_approval_only", "_comment", "_note",
})

# Used when a platform is switched on but names no cap at all. NOT
# unlimited: an enabled platform with no stated limit is far more likely
# to be an oversight than a decision to post without bound.
FALLBACK_DAILY_CAP = 5


def daily_cap_of(settings: dict) -> int:
    """This platform's daily cap, where 0 means no limit.

    An explicit 0 is honoured: writing it is a decision. A MISSING cap on
    an enabled platform is not a decision, it is an oversight, and the
    two used to be indistinguishable - which is how a block that plainly
    said 3 ran unlimited for a week under a key nothing read.
    """
    settings = settings or {}
    for key in CAP_KEYS:
        if key in settings:
            return int(settings.get(key) or 0)
    if settings.get("enabled", False):
        return FALLBACK_DAILY_CAP
    return 0


def unknown_keys(settings: dict) -> list:
    """Keys in a platform block that nothing reads."""
    return sorted(k for k in (settings or {}) if k not in KNOWN_PLATFORM_KEYS)


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

    config: Dict[str, Any] = field(default_factory=dict)
    state_path: str = "./posting_state.json"
    _state: dict = field(default_factory=dict, init=False)

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 state_path: Optional[str] = None) -> None:
        # Positional (cfg, state_path) is how publishers/ construct this;
        # keyword config=/state_path= is how the tests do. Accepting the
        # whole app config OR just its "posting" block means callers don't
        # have to remember which level they're holding.
        config = config or {}
        self.config = config.get("posting", config) if isinstance(config, dict) else {}
        self.state_path = str(state_path or self.config.get(
            "state_path", "./posting_state.json"))
        self._state = {}
        self._load()

    # ── State ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError):
            loaded = {}
        self._state = loaded if isinstance(loaded, dict) else {}
        self._migrate_legacy_state()
        self._state.setdefault("posts", {})       # platform -> [timestamps]
        self._state.setdefault("failures", {})    # platform -> consecutive count

    def _migrate_legacy_state(self) -> None:
        """Absorb the older per-platform state layout.

        An earlier version stored {"instagram": {"posts": [...],
        "consecutive_failures": N}}. Dropping those records on upgrade
        would reset the caps to zero, which is exactly the burst this
        module exists to prevent - so they are folded in rather than
        ignored.
        """
        legacy = {
            name: value for name, value in self._state.items()
            if name not in ("posts", "failures")
            and isinstance(value, dict) and "posts" in value
        }
        if not legacy:
            return
        posts = self._state.setdefault("posts", {})
        failures = self._state.setdefault("failures", {})
        for name, value in legacy.items():
            stamps = [t for t in (value.get("posts") or [])
                      if isinstance(t, (int, float))]
            posts[name] = sorted(set(posts.get(name, [])) | set(stamps))
            failures[name] = max(int(failures.get(name, 0) or 0),
                                 int(value.get("consecutive_failures", 0) or 0))
            del self._state[name]

    def _save(self) -> None:
        """Atomic write - a crash mid-save must not lose the post history,
        because losing it resets the caps and permits a burst."""
        directory = os.path.dirname(os.path.abspath(self.state_path))
        os.makedirs(directory, exist_ok=True)
        tmp = f"{self.state_path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
            os.replace(tmp, self.state_path)   # atomic on POSIX and Windows
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

    def kill_switch_engaged(self) -> tuple:
        """(engaged, reason).

        Two forms on purpose. The config flag is the deliberate one; the
        file is the panic one - it stops a long-running --watch without
        editing JSON or hunting for the process, and it works even if the
        config is mid-edit.
        """
        if not self.config.get("enabled", False):
            return True, "posting.enabled is false in config"
        path = self.config.get("kill_switch_file") or DEFAULT_KILL_SWITCH_FILE
        if os.path.exists(path):
            return True, f"kill switch file present: {path}"
        return False, ""

    # ── Rolling window ───────────────────────────────────────────────────

    def _recent_posts(self, platform: str, now: Optional[float] = None) -> list:
        now = time.time() if now is None else now
        stamps = self._state["posts"].get(platform) or []
        fresh = [t for t in stamps
                 if isinstance(t, (int, float)) and now - t < WINDOW_SECONDS]
        if len(fresh) != len(stamps):
            self._state["posts"][platform] = fresh
        return sorted(fresh)

    def posts_in_window(self, platform: str, now: Optional[float] = None) -> int:
        return len(self._recent_posts(platform, now))

    def consecutive_failures(self, platform: str) -> int:
        return int(self._state["failures"].get(platform, 0) or 0)

    # ── The decision ─────────────────────────────────────────────────────

    def check(self, platform: str, now: Optional[float] = None,
              ignore_spacing: bool = False) -> Decision:
        """May `platform` be posted to right now?

        Checked cheapest-and-most-absolute first, so a kill switch beats
        everything and no amount of per-platform config can override it.

        `ignore_spacing` waives ONLY the minimum gap between posts, and
        only for a person running a single command by hand. Spacing
        exists so an automated run does not fire a burst; one deliberate
        post is not a burst. The kill switch, the daily cap, the
        manual-only rule and the circuit breaker all still apply - those
        are about whether a post should happen at all, which a human
        being present does not change.
        """
        now = time.time() if now is None else now

        engaged, why = self.kill_switch_engaged()
        if engaged:
            return Decision(False, f"KILL SWITCH: {why}")

        # Before the enabled check, so a manual-only platform says why it
        # is parked rather than the less useful "disabled".
        if self.is_manual_only(platform):
            return Decision(
                False,
                f"{platform} is manual-approval only - queued for a human, "
                "never auto-posted")

        settings = self._platform_config(platform)
        if not settings:
            # Distinct from "disabled": a typo'd platform name would
            # otherwise look like a deliberate off switch.
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
        cap = daily_cap_of(settings)
        if cap > 0 and len(recent) >= cap:
            oldest = recent[0]
            wait = max(0.0, (oldest + WINDOW_SECONDS) - now)
            return Decision(
                False,
                f"{platform} daily cap reached ({len(recent)}/{cap} in the last 24h)",
                retry_after_s=wait)

        spacing_min = float(settings.get("min_minutes_between", 0) or 0)
        if spacing_min > 0 and recent and not ignore_spacing:
            since = now - recent[-1]
            needed = spacing_min * 60
            if since < needed:
                return Decision(
                    False,
                    f"{platform} spacing: posted {since / 60:.0f} min ago, "
                    f"minimum is {spacing_min:.0f} min",
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
        """Clear the circuit breaker, deliberately and manually.

        Not on a timer. A platform failing three times running is a signal
        that something is wrong at the account or credential level, and an
        auto-reset would just walk back into it every hour - which is
        itself the behaviour that gets an account flagged.
        """
        if platform is None:
            self._state["failures"] = {}
        else:
            self._state["failures"].pop(platform, None)
        self._save()

    # ── Publisher-facing API ─────────────────────────────────────────────

    def can_post(self, platform: str, ignore_spacing: bool = False) -> tuple:
        """(allowed, reason). reason is "" when allowed.

        Same checks as check(); this shape is what publishers/ expects.
        """
        decision = self.check(platform, ignore_spacing=ignore_spacing)
        return decision.allowed, ("" if decision.allowed else decision.reason)

    def record_result(self, platform: str, success: bool) -> None:
        """Call after every post attempt, success or failure."""
        if success:
            self.record_post(platform)
        else:
            self.record_failure(platform)

    # ── Reporting ────────────────────────────────────────────────────────

    def status(self, platforms: Optional[Iterable[str]] = None) -> list:
        """(platform, allowed, reason) per platform, for `--posting-status`."""
        names = list(platforms) if platforms else sorted(
            (self.config.get("platforms") or {}).keys())
        return [(name, d.allowed, d.reason)
                for name, d in ((n, self.check(n)) for n in names)]


def engage_kill_switch(config: dict, note: str = "") -> str:
    """Create the kill-switch file. Returns the path written."""
    config = config.get("posting", config)
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
    config = config.get("posting", config)
    path = config.get("kill_switch_file") or DEFAULT_KILL_SWITCH_FILE
    try:
        os.remove(path)
        return True
    except OSError:
        return False

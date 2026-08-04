"""
publish_guard.py — Gate every outgoing post through this module.

Checks (in order):
  1. Global kill switch (config flag OR STOP_POSTING file on disk)
  2. Platform enabled flag
  3. Rolling 24-hour cap  (NOT a calendar-day reset)
  4. Minimum spacing between posts on that platform
  5. Circuit breaker     (trips after N consecutive failures)

If any check fails the post is deferred/blocked — it does NOT count
against the retry budget stored in job_queue.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Any

log = logging.getLogger("publish_guard")

# ── internal helpers ───────────────────────────────────────────────────────────

def _load_state(state_path: Path) -> Dict[str, Any]:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except Exception:
            pass
    return {}


def _save_state(state_path: Path, state: Dict[str, Any]) -> None:
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(state_path)  # atomic on POSIX; best-effort on Windows


def _platform_state(state: Dict, platform: str) -> Dict:
    return state.setdefault(platform, {
        "posts": [],          # list of epoch timestamps (rolling window)
        "last_post_ts": 0,
        "consecutive_failures": 0,
        "circuit_open": False,
        "circuit_open_since": 0,
    })


# ── public API ─────────────────────────────────────────────────────────────────

class PublishGuard:
    """
    Usage:
        guard = PublishGuard(cfg, state_path)
        allowed, reason = guard.can_post("instagram")
        if allowed:
            ok = do_post()
            guard.record_result("instagram", success=ok)
    """

    CIRCUIT_RESET_SECONDS = 3600  # auto-reset circuit breaker after 1 h

    def __init__(self, cfg: Dict[str, Any], state_path: str | Path) -> None:
        self._cfg = cfg
        self._state_path = Path(state_path)
        self._posting_cfg = cfg.get("posting", {})
        self._platforms_cfg = self._posting_cfg.get("platforms", {})
        self._kill_file = Path(
            self._posting_cfg.get("kill_switch_file", "./STOP_POSTING")
        )
        self._circuit_threshold = (
            self._posting_cfg
            .get("circuit_breaker", {})
            .get("consecutive_failures", 3)
        )

    # ── checks ────────────────────────────────────────────────────────────────

    def _global_enabled(self) -> tuple[bool, str]:
        if not self._posting_cfg.get("enabled", False):
            return False, "posting.enabled is false in config"
        if self._kill_file.exists():
            return False, f"kill switch file present: {self._kill_file}"
        return True, ""

    def _platform_enabled(self, platform: str) -> tuple[bool, str]:
        pcfg = self._platforms_cfg.get(platform, {})
        if not pcfg.get("enabled", False):
            return False, f"{platform} is disabled in config"
        if pcfg.get("manual_approval_only", False):
            return False, f"{platform} is manual-approval only — cannot be automated"
        return True, ""

    def _cap_ok(self, platform: str, pstate: Dict) -> tuple[bool, str]:
        pcfg = self._platforms_cfg.get(platform, {})
        cap = pcfg.get("daily_cap", 5)
        window = 86400  # 24 hours in seconds
        now = time.time()
        recent = [t for t in pstate["posts"] if now - t < window]
        pstate["posts"] = recent  # prune old
        if len(recent) >= cap:
            oldest = min(recent)
            resets_in = int(window - (now - oldest))
            return False, (
                f"{platform} 24h cap reached ({len(recent)}/{cap}); "
                f"resets in {resets_in // 60} min"
            )
        return True, ""

    def _spacing_ok(self, platform: str, pstate: Dict) -> tuple[bool, str]:
        pcfg = self._platforms_cfg.get(platform, {})
        min_min = pcfg.get("min_minutes_between", 0)
        min_sec = min_min * 60
        if min_sec <= 0:
            return True, ""
        elapsed = time.time() - pstate.get("last_post_ts", 0)
        if elapsed < min_sec:
            wait = int(min_sec - elapsed)
            return False, (
                f"{platform} spacing: must wait {wait // 60} more min"
            )
        return True, ""

    def _circuit_ok(self, platform: str, pstate: Dict) -> tuple[bool, str]:
        if not pstate.get("circuit_open", False):
            return True, ""
        # auto-reset after CIRCUIT_RESET_SECONDS
        age = time.time() - pstate.get("circuit_open_since", 0)
        if age >= self.CIRCUIT_RESET_SECONDS:
            pstate["circuit_open"] = False
            pstate["consecutive_failures"] = 0
            log.info("%s circuit breaker auto-reset after %ds", platform, int(age))
            return True, ""
        return False, (
            f"{platform} circuit breaker open after "
            f"{pstate['consecutive_failures']} consecutive failures; "
            f"auto-resets in {int(self.CIRCUIT_RESET_SECONDS - age) // 60} min"
        )

    # ── public methods ────────────────────────────────────────────────────────

    def can_post(self, platform: str) -> tuple[bool, str]:
        """Return (allowed, reason_string). reason is empty string when allowed."""
        ok, reason = self._global_enabled()
        if not ok:
            log.debug("[guard] blocked %s — %s", platform, reason)
            return False, reason

        ok, reason = self._platform_enabled(platform)
        if not ok:
            log.debug("[guard] blocked %s — %s", platform, reason)
            return False, reason

        state = _load_state(self._state_path)
        pstate = _platform_state(state, platform)

        for check in (self._cap_ok, self._spacing_ok, self._circuit_ok):
            ok, reason = check(platform, pstate)
            if not ok:
                _save_state(self._state_path, state)  # persist pruned cap list
                log.info("[guard] blocked %s — %s", platform, reason)
                return False, reason

        _save_state(self._state_path, state)
        return True, ""

    def record_result(self, platform: str, success: bool) -> None:
        """Call this after every post attempt (success or failure)."""
        state = _load_state(self._state_path)
        pstate = _platform_state(state, platform)
        now = time.time()

        if success:
            pstate["posts"].append(now)
            pstate["last_post_ts"] = now
            pstate["consecutive_failures"] = 0
            pstate["circuit_open"] = False
            log.info("[guard] recorded success for %s (cap used: %d)",
                     platform, len(pstate["posts"]))
        else:
            pstate["consecutive_failures"] = pstate.get("consecutive_failures", 0) + 1
            if pstate["consecutive_failures"] >= self._circuit_threshold:
                pstate["circuit_open"] = True
                pstate["circuit_open_since"] = now
                log.warning(
                    "[guard] %s circuit breaker OPEN after %d consecutive failures",
                    platform, pstate["consecutive_failures"]
                )
            else:
                log.warning(
                    "[guard] %s failure %d/%d before circuit opens",
                    platform, pstate["consecutive_failures"], self._circuit_threshold
                )

        _save_state(self._state_path, state)

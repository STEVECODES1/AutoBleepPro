"""Stay out of the way while the owner plays (11-05: "I don't want it
lagging my PC while playing the game - run everything 1 by 1").

* start(): the uploader runs at BELOW NORMAL priority, and at IDLE (with
  every ffmpeg / python it started) while a game is running - the game
  always gets the CPU first.
* wait_if_gaming(what): the big GPU jobs (a full stream's censor pass,
  cutting clips) wait until the game closes - checked every minute, at
  most MAX_WAIT_S, then they run anyway so nothing is stuck for good.
Game = a running process whose name matches GAME_HINTS (GTA V, FiveM,
RageMP, alt:V...) or general.game_processes in config.json. Never raises.
"""

from __future__ import annotations

import threading
import time

# name STARTS with one of these (FiveM_b3095_GTAProcess, GTA5_Enhanced...)
GAME_HINTS = ("gta5", "gtav", "playgtav", "fivem", "ragemp", "altv",
              "redm", "rdr2", "valorant", "fortniteclient", "r5apex",
              "eldenring", "blackops", "modernwarfare")
# exact names only (too short to match by prefix - "cod" is also code.exe)
GAME_EXACT = ("cod", "cs2", "minecraft")
EXTRA: list = []
MAX_WAIT_S = 6 * 3600
_started = False


def game_running() -> str:
    """The game's process name, or ""."""
    try:
        import psutil

        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "").lower()
            if not name.endswith(".exe"):
                continue
            stem = name[:-4]
            if any(stem.startswith(h) for h in GAME_HINTS) or stem in GAME_EXACT or \
                    any(stem == e.lower().replace(".exe", "") for e in EXTRA):
                return name
    except Exception:
        pass
    return ""


def _set_priority(idle: bool) -> None:
    try:
        import psutil

        level = psutil.IDLE_PRIORITY_CLASS if idle else psutil.BELOW_NORMAL_PRIORITY_CLASS
        me = psutil.Process()
        for p in [me] + me.children(recursive=True):
            try:
                if p.nice() != level:
                    p.nice(level)
            except Exception:
                pass
    except Exception:
        pass


def start(cfg=None) -> None:
    global _started
    if _started:
        return
    _started = True
    try:
        extra = getattr(getattr(cfg, "general", None), "game_processes", None) or []
        EXTRA.extend(str(x) for x in extra)
    except Exception:
        pass

    def loop():
        was = None
        while True:
            game = game_running()
            if bool(game) != was:
                was = bool(game)
                print(f"[Game] {game} is running - the uploader steps back "
                      f"(lowest priority, big jobs wait)." if game else
                      "[Game] No game running - normal pace.")
            _set_priority(bool(game))
            time.sleep(20)

    threading.Thread(target=loop, daemon=True, name="game-mode").start()


def wait_if_gaming(what: str, max_wait_s: float = MAX_WAIT_S) -> None:
    """Block while a game runs (at most max_wait_s)."""
    game = game_running()
    if not game:
        return
    print(f"[Game] {game} is running - {what} waits until you stop playing "
          f"(at most {max_wait_s / 3600:.0f} h).")
    start_t = time.time()
    while game_running() and time.time() - start_t < max_wait_s:
        time.sleep(60)
    print(f"[Game] Going ahead with {what}.")

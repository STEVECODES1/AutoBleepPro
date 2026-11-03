"""Collab every new wopovod full stream with STACKSWOPO GAMES - on its own,
no Claude needed (11-03: the daily Claude task was off and the 11-02
stream went up with no collab).

YouTube has no API for collaborations, so this drives the 9222 Chrome the
way utils/community_post does:

1. As STACKSWOPO VODS: Studio video edit -> Show more -> Invite a
   collaborator -> search "StackswopoGames" -> pick it -> Save (the dialog)
   -> Save (the video).
2. Switch to STACKSWOPO GAMES, open its collaboration requests from wopovod,
   Accept.
3. ALWAYS switch back to STACKSWOPO VODS (posts and Studio work need it).
4. Done when the watch page names both channels.

One collab per video, ever (logs/collabs.json). Runs as a process of its
own after the upload (post_later), patient like the Posts-tab post.
"""

import json
import os
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(_ROOT, "logs", "collabs.json")
LOG = os.path.join(_ROOT, "logs", "collab.log")
GAMES_HANDLE = "StackswopoGames"
GAMES_NAME = "STACKSWOPO GAMES"
VODS_NAME = "STACKSWOPO VODS"
GAMES_ID = "UCKj0xrFqNNXpBCl_B1shckA"
VODS_ID = "UCbS-_v6Y9AZmAAU3EpzLsvA"
REQUESTS_URL = (f"https://studio.youtube.com/channel/{GAMES_ID}"
                f"/collaboration/{VODS_ID}")
_LOCK = threading.Lock()


def _load(path: str = "") -> dict:
    try:
        with open(path or LEDGER, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def done(vid: str, ledger: str = "") -> bool:
    return bool((_load(ledger).get(vid) or {}).get("accepted"))


def _note(vid: str, entry: dict, ledger: str = "") -> None:
    with _LOCK:
        data = _load(ledger)
        data[vid] = {**(data.get(vid) or {}), **entry}
        path = ledger or LEDGER
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)


def _visible_button(scope, text):
    return scope.locator("ytcp-button:visible, button:visible",
                         has_text=text).first


def invite(page, vid: str, say=print) -> bool:
    """Step 1, as STACKSWOPO VODS. True when the invite is saved (or was
    already there)."""
    page.goto(f"https://studio.youtube.com/video/{vid}/edit",
              wait_until="domcontentloaded", timeout=60_000)
    page.bring_to_front()
    page.wait_for_selector("ytcp-button#toggle-button", timeout=60_000)
    page.wait_for_timeout(2_000)
    page.evaluate("() => document.querySelector('ytcp-button#toggle-button').click()")
    button = page.locator("ytcp-button#collaboration-button").first
    button.wait_for(timeout=20_000)
    section = page.evaluate(
        "() => { const b = document.querySelector('ytcp-button#collaboration-button');"
        " const s = b && b.closest('.section, ytcp-form-input-container, div');"
        " return (s ? s.parentElement.innerText : '') }") or ""
    if GAMES_NAME.lower() in section.lower():
        say("[Collab] STACKSWOPO GAMES is already invited.")
        return True
    button.scroll_into_view_if_needed()
    button.click()
    dialog = page.locator("ytcp-dialog#collaborators-dialog").first
    search = dialog.locator("input[aria-label='Search for a channel']").first
    search.wait_for(timeout=20_000)
    search.click()
    page.keyboard.type(GAMES_HANDLE, delay=40)
    option = page.locator("tp-yt-paper-item:visible, [role=option]:visible",
                          has_text=GAMES_NAME).first
    option.wait_for(timeout=20_000)
    option.click()
    page.wait_for_timeout(1_500)
    # Picking the channel opens "Copy and share your invite link" (some
    # layouts first need "Create link"). Close it with its X - it sits over
    # the dialog's Save.
    try:
        link = _visible_button(page, "Create link")
        if link.count():
            link.click(timeout=5_000)
            page.wait_for_timeout(2_000)
    except Exception:
        pass
    for _ in range(3):
        if not page.get_by_text("Copy and share your invite link").first.is_visible():
            break
        closed = page.evaluate(
            "() => { const h = [...document.querySelectorAll('*')].find(e =>"
            " e.children.length === 0 && e.textContent.trim() === 'Copy and share your invite link'"
            " && e.offsetParent);"
            " let d = h; for (let i = 0; d && i < 8; i++) { d = d.parentElement;"
            "  const b = d && d.querySelector('button[aria-label=\"Close\"], ytcp-icon-button#close-button,"
            " [aria-label=\"Close\"]'); if (b) { b.click(); return true; } } return false; }")
        if not closed:
            page.keyboard.press("Escape")
        page.wait_for_timeout(1_500)
    save_dialog = dialog.locator("ytcp-button#save-button").first
    if save_dialog.is_visible():
        save_dialog.click(timeout=10_000)
        page.wait_for_timeout(2_000)
    save = page.locator("ytcp-button#save:visible").first
    try:
        save.wait_for(timeout=10_000)
        if save.is_enabled():
            save.click(timeout=10_000)
    except Exception:
        pass
    try:
        page.get_by_text("Changes saved").first.wait_for(timeout=30_000)
    except Exception:
        say("[Collab] Saved, but Studio did not say 'Changes saved'.")
    say("[Collab] Invite sent to STACKSWOPO GAMES.")
    return True


def accept(page, say=print) -> bool:
    """Step 2, as STACKSWOPO GAMES. Accepts what is waiting from wopovod."""
    page.goto(REQUESTS_URL, wait_until="domcontentloaded", timeout=60_000)
    page.bring_to_front()
    # The link redirects to Content and opens the request as a dialog
    # (Decline | Accept) - that takes 10+ s.
    try:
        page.locator("button:visible, ytcp-button:visible",
                     has_text="Accept").first.wait_for(timeout=45_000)
    except Exception:
        pass
    accepted = 0
    for _ in range(10):
        # One Accept can take every pending request (and the dialog closes),
        # so a button that is gone after a click is the normal end.
        btn = page.locator("button:visible", has_text="Accept").last
        if not btn.count():
            break
        try:
            btn.click(timeout=10_000)
        except Exception:
            break
        accepted += 1
        page.wait_for_timeout(4_000)
    say(f"[Collab] Accepted {accepted} request(s) as STACKSWOPO GAMES.")
    return accepted > 0


def shown_on_watch_page(page, vid: str) -> bool:
    page.goto(f"https://www.youtube.com/watch?v={vid}",
              wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(8_000)
    text = page.evaluate(
        "() => [...document.querySelectorAll('ytd-video-owner-renderer, #owner')]"
        ".map(e => e.innerText).join(' | ')") or ""
    return f"and {GAMES_NAME}".lower() in text.lower()


def run(video_url: str, cdp_url: str = "http://localhost:9222",
        ledger: str = "", say=print) -> bool:
    from utils.community_post import _switch_to, video_id

    vid = video_id(video_url)
    if not vid or done(vid, ledger):
        return bool(vid)
    from playwright.sync_api import sync_playwright

    from utils.chrome_cdp import ensure_chrome

    ready, detail = ensure_chrome(cdp_url)
    if not ready:
        say(f"[Collab] Chrome is not up ({detail}).")
        return False
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url, timeout=45_000)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            if not (_load(ledger).get(vid) or {}).get("invited"):
                _switch_to(page, VODS_NAME)
                if invite(page, vid, say):
                    _note(vid, {"invited": time.time()}, ledger)
            if not _switch_to(page, GAMES_NAME):
                say("[Collab] Could not switch the window to STACKSWOPO GAMES.")
                return False
            accept(page, say)
            _switch_to(page, VODS_NAME)
            if shown_on_watch_page(page, vid):
                _note(vid, {"accepted": time.time()}, ledger)
                say(f"[Collab] Done: {vid} shows STACKSWOPO VODS and STACKSWOPO GAMES.")
                return True
            say("[Collab] Not on the watch page yet - trying again later.")
            return False
        except Exception as exc:
            say(f"[Collab] failed ({str(exc).splitlines()[0][:160]}).")
            try:
                page.screenshot(path=os.path.join(_ROOT, "logs", "collab_failed.png"))
            except Exception:
                pass
            return False
        finally:
            try:
                _switch_to(page, VODS_NAME)  # never leave the window on GAMES
            except Exception:
                pass
            try:
                page.close()
            except Exception:
                pass


def post_later(video_url: str, wait_s: float = 600, tries: int = 12,
               between_s: float = 1800) -> None:
    """After a new full stream: its own process, so nothing on the upload
    path waits and an uploader restart cannot lose it."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    import subprocess
    import sys

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as log:
        subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "utils.collab", video_url,
             str(wait_s), str(tries), str(between_s)],
            stdout=log, stderr=subprocess.STDOUT, cwd=_ROOT,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


if __name__ == "__main__":
    import sys

    sys.path.insert(0, _ROOT)
    sys.path.insert(0, os.path.dirname(_ROOT))
    os.chdir(_ROOT)
    url = sys.argv[1]
    wait_s = float(sys.argv[2]) if len(sys.argv) > 2 else 0
    tries = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    between = float(sys.argv[4]) if len(sys.argv) > 4 else 1800
    print(f"{time.strftime('%m-%d %H:%M:%S')} collab for {url}", flush=True)
    time.sleep(wait_s)
    from utils.live_now import is_live

    for n in range(tries):
        try:
            # Never drive the browser while he is live - the relay/recorder
            # windows matter more.
            if is_live():
                time.sleep(between)
                continue
        except Exception:
            pass
        if run(url, say=lambda s: print(s, flush=True)):
            break
        time.sleep(between)
    print("result: done" if done(__import__("utils.community_post",
          fromlist=["video_id"]).video_id(url)) else "result: failed", flush=True)

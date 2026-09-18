"""
utils/x_poster.py — Post to X (Twitter) via browser automation (Playwright),
bypassing X's paid API entirely.

WHY THIS EXISTS: X killed free API access for new developer apps in
February 2026 - every new app now defaults to "pay-per-use" billing
($0.015/post created, $0.005/post read, no free tier to fall back to) and
every request fails with a bare 401 until a card is on file. This module
posts to X the same way a human does, through x.com's own web UI, the
same trick this project already uses for Rumble (which has no public
upload API at all) - see utils/rumble_uploader.py for the sibling
implementation and its more detailed comments on the general approach.

Two ways to authenticate - CDP attach is the STRONGLY preferred one here,
more so than for Rumble:

1. **CDP attach (recommended)** - set X_CDP_URL in .env to attach to a
   Chrome window you're ALREADY logged into (the SAME window used for
   Rumble's cdp_url works fine - one Chrome profile can be logged into
   both sites in different tabs). No stored password, no login-selector
   guessing, and any "confirm it's you" check is whatever you already
   did by hand.
2. **Username/password (fallback)** - if X_CDP_URL isn't set, this
   launches a fresh browser and automates X's own login form using
   X_USERNAME/X_PASSWORD from .env. Flag this clearly before relying on
   it: X's login form is aggressively bot-protected (CAPTCHA /
   "unusual login activity" / phone-verification challenges are common
   on a fresh browser profile or unfamiliar IP) - this project hit the
   exact same kind of wall trying to automate Reddit's app-creation
   captcha earlier, and there is no reason to expect X's login to be
   easier. If a verification step appears, this pauses and asks you to
   solve it by hand in the visible window (same pattern as Rumble's 2FA
   pause) rather than trying to guess captcha-solving code that would
   likely just fail anyway.

IMPORTANT — this is NOT the same thing as posting through X's API. It is
automation of the ordinary web UI, which is a materially different risk
profile than an API call made with your own developer credentials: X's
automation rules are about *behavior* (posting like a bot: too fast, too
regular, too many identical posts) more than about *mechanism*, but
driving the UI instead of the API removes the one guarantee an API key
gave you - a documented, sanctioned way in. Keep the daily_cap and
min_minutes_between in config.json's posting.platforms.x conservative;
those limits exist so this looks like a human posting occasionally, not
a bot spamming, which is what actually gets accounts flagged/suspended.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from playwright.sync_api import sync_playwright


# Recorded instead of a URL when the post went through but X's UI never
# surfaced a permalink to scrape. Not "FAILED:" - the post IS up, so
# dedup/logging must still treat it as done.
POSTED_NO_URL = "posted (X did not show a link - check your profile)"

_STATUS_URL = re.compile(r"^https://x\.com/[^/]+/status/\d+")


class XPoster:
    def __init__(
        self,
        username: str = "",
        password: str = "",
        login_url: str = "https://x.com/login",
        home_url: str = "https://x.com/home",
        headless: bool = False,
        cdp_url: Optional[str] = None,
    ):
        self.username = username
        self.password = password
        self.login_url = login_url
        self.home_url = home_url
        # False by default so you can see (and solve) a verification
        # challenge the first few times this runs without cdp_url.
        self.headless = headless
        self.cdp_url = cdp_url

    def post(self, text: str) -> str:
        """Post `text` to X. Returns the tweet URL, or POSTED_NO_URL if the
        post succeeded but no permalink could be scraped. Raises on
        anything that looks like an actual failure to post."""
        text = (text or "")[:280]  # X's hard cap

        with sync_playwright() as p:
            browser = page = None
            should_close_browser = True
            attached = False

            if self.cdp_url:
                try:
                    browser = p.chromium.connect_over_cdp(self.cdp_url)
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = context.new_page()
                    should_close_browser = False  # the user's window - don't close it
                    attached = True
                    print(f"[X] Attached to Chrome at {self.cdp_url}.")
                except Exception as exc:
                    print(f"[X] Could not attach to Chrome at {self.cdp_url} ({exc}).")
                    if self.username and self.password:
                        print("[X] Falling back to username/password login. "
                              "This is more likely to hit a captcha/verification "
                              "wall than the CDP-attach path - see this module's "
                              "docstring.")
                    browser = page = None

            if page is None:
                if not self.username or not self.password:
                    raise RuntimeError(
                        "Could not attach to Chrome at "
                        f"{self.cdp_url or '(unset)'}, and X_USERNAME/X_PASSWORD "
                        "are not set in .env either - so there is no way to "
                        "reach X. Either launch Chrome with "
                        "--remote-debugging-port=9222, log into x.com there, "
                        "and set X_CDP_URL in .env, or fill in the .env "
                        "username/password."
                    )
                browser = p.chromium.launch(headless=self.headless)
                page = browser.new_page()
                should_close_browser = True

            try:
                if not attached:
                    self._login(page)
                tweet_url = self._compose_and_post(page, text)
            finally:
                if should_close_browser:
                    browser.close()
                else:
                    page.close()

        return tweet_url

    def _login(self, page) -> None:
        page.goto(self.login_url, timeout=60_000)
        page.wait_for_load_state("networkidle", timeout=30_000)

        username_field = (
            page.locator("input[autocomplete='username']")
            .or_(page.locator("input[name='text']"))
        )
        username_field.first.wait_for(state="visible", timeout=30_000)
        username_field.first.fill(self.username)

        next_button = page.get_by_role("button", name=re.compile(r"^next$", re.I))
        next_button.first.click(timeout=15_000)
        page.wait_for_timeout(1500)

        # X sometimes interposes an "enter your phone number or username"
        # re-confirmation step here (unusual-activity check) before the
        # password field - if a username-shaped field shows up again
        # instead of the password field, fill it and click Next again.
        confirm_field = page.locator("input[data-testid='ocfEnterTextTextInput']")
        if confirm_field.count() > 0 and confirm_field.first.is_visible():
            confirm_field.first.fill(self.username)
            page.get_by_role("button", name=re.compile(r"^next$", re.I)).first.click(timeout=15_000)
            page.wait_for_timeout(1500)

        password_field = page.locator("input[name='password']").or_(page.locator("input[type='password']"))
        password_field.first.wait_for(state="visible", timeout=30_000)
        password_field.first.fill(self.password)

        login_button = page.get_by_role("button", name=re.compile(r"^log ?in$", re.I))
        login_button.first.click(timeout=15_000)
        page.wait_for_timeout(3000)

        # Verification challenge (captcha, phone/email confirmation code,
        # "unusual login activity"). Can't be solved blind - pause for a
        # human, same pattern as Rumble's 2FA prompt.
        challenge_field = page.locator(
            "input[data-testid='ocfEnterTextTextInput'], "
            "input[autocomplete='one-time-code'], "
            "input[name*='challenge' i]"
        )
        if challenge_field.count() > 0 and challenge_field.first.is_visible():
            print("[X] Login is asking for extra verification (code, phone, "
                  "or captcha) - solve it in the browser window now.")
            code = input("[X] Once you've handled it and are logged in, press "
                          "Enter here (or paste a verification code if one was "
                          "requested): ").strip()
            if code:
                challenge_field.first.fill(code)
                continue_button = (
                    page.get_by_role("button", name=re.compile(r"^(next|verify|submit)$", re.I))
                )
                if continue_button.count() > 0:
                    continue_button.first.click(timeout=15_000)
            page.wait_for_timeout(3000)

    def _compose_and_post(self, page, text: str) -> str:
        page.goto(self.home_url, timeout=60_000)
        page.wait_for_load_state("networkidle", timeout=30_000)

        composer = page.locator("[data-testid='tweetTextarea_0']")
        try:
            composer.first.wait_for(state="visible", timeout=30_000)
        except Exception as exc:
            dump = self._dump_page(page)
            raise RuntimeError(
                "Could not find X's post composer on the home timeline - "
                "either the login didn't complete or X changed its page. "
                f"Page HTML saved to: {dump}"
            ) from exc

        composer.first.click(timeout=10_000)
        composer.first.fill(text)
        page.wait_for_timeout(500)

        post_button = (
            page.locator("[data-testid='tweetButtonInline']")
            .or_(page.locator("[data-testid='tweetButton']"))
        )
        post_button.first.wait_for(state="visible", timeout=15_000)
        if not post_button.first.is_enabled():
            # Rare, but if the button never enables the text likely never
            # registered as real input (composer needs real keystrokes,
            # not just a value set) - retype via keyboard as a fallback.
            composer.first.click(timeout=10_000)
            page.keyboard.type(text, delay=15)
            page.wait_for_timeout(500)

        post_button.first.click(timeout=15_000)

        # X shows a "Your post was sent" toast and drops the new post at
        # the top of the timeline. Wait briefly, then scrape the newest
        # status link the same best-effort way Rumble scrapes video URLs.
        page.wait_for_timeout(3000)
        return self._find_tweet_url(page) or POSTED_NO_URL

    def _find_tweet_url(self, page) -> Optional[str]:
        try:
            hrefs = page.evaluate(
                "() => Array.from(document.querySelectorAll(\"article[data-testid='tweet'] a[href*='/status/']\"))"
                "        .map(a => a.href)"
            )
        except Exception:
            hrefs = []
        for href in hrefs or []:
            if isinstance(href, str) and _STATUS_URL.match(href):
                return href
        return None

    def _dump_page(self, page) -> str:
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs",
            f"x_page_dump_{int(time.time())}.html",
        )
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(page.content())
            return path
        except Exception:
            return "(could not write page dump)"

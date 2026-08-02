"""
Rumble uploader via browser automation (Playwright).

IMPORTANT - read this before relying on it: Rumble has no public API for
regular creators to upload programmatically, unlike YouTube's official,
documented Data API. This module drives Rumble's own web upload form the
same way a human would. That means it's fragile if Rumble redesigns their
page, and if Rumble ever changes it and a step below fails, the fastest
fix is: `playwright codegen https://rumble.com/upload.php` in a terminal,
manually click through an upload, and update the locator on the matching
line here from what codegen records.

Two ways to authenticate:

1. **CDP attach (recommended)** - set `rumble.cdp_url` in config.json (e.g.
   "http://localhost:9222") and launch Chrome yourself with remote
   debugging enabled, log into Rumble in that window like normal, and
   leave it open. This uploader then attaches to YOUR already-logged-in
   session instead of automating the login form at all - no stored
   password needed, no login-selector guessing, and 2FA is simply
   whatever you already did manually. See README.md for the exact
   command to launch Chrome this way.
2. **Username/password (fallback)** - if `rumble.cdp_url` isn't set, this
   launches a fresh browser and automates the login form using your
   RUMBLE_USERNAME/RUMBLE_PASSWORD from .env. More fragile (depends on
   guessing the right login-form selectors) and needs your password
   stored locally. If 2FA is prompted, this pauses and asks you to type
   the code into the terminal.
"""

import os
import time
from typing import Callable, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


class RumbleUploader:
    def __init__(
        self,
        username: str,
        password: str,
        login_url: str,
        upload_url: str,
        headless: bool = False,
        cdp_url: Optional[str] = None,
    ):
        self.username = username
        self.password = password
        self.login_url = login_url
        self.upload_url = upload_url
        # Headless=False by default so you can see what's happening (and
        # solve 2FA/captchas manually) the first few times you run this.
        self.headless = headless
        self.cdp_url = cdp_url

    def upload(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: list,
        privacy: str = "public",
        thumbnail_path: Optional[str] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> str:
        with sync_playwright() as p:
            if self.cdp_url:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                should_close_browser = False  # it's the user's own Chrome window - don't close it
            else:
                if not self.username or not self.password:
                    raise RuntimeError(
                        "Neither rumble.cdp_url (config.json) nor RUMBLE_USERNAME/RUMBLE_PASSWORD (.env) are set."
                    )
                browser = p.chromium.launch(headless=self.headless)
                page = browser.new_page()
                should_close_browser = True

            try:
                if not self.cdp_url:
                    self._login(page)
                video_url = self._upload_video(
                    page, video_path, title, description, tags, privacy, thumbnail_path, progress_callback
                )
            finally:
                if should_close_browser:
                    browser.close()
                else:
                    page.close()

        return video_url

    def _login(self, page) -> None:
        page.goto(self.login_url, timeout=60_000)
        # Rumble's login redirects to a JS-rendered auth subdomain; give it
        # a moment to finish rendering the form before we try to find fields.
        page.wait_for_load_state("networkidle", timeout=30_000)

        # Type-based selectors first (input[type=email/password] is nearly
        # universal across login forms regardless of exact markup/labels),
        # falling back to label/id guesses.
        username_field = (
            page.locator("input[type='email']")
            .or_(page.locator("input[name*='user' i]"))
            .or_(page.locator("input[name*='email' i]"))
            .or_(page.get_by_label("Username or Email"))
            .or_(page.locator("#login-username"))
        )
        password_field = (
            page.locator("input[type='password']")
            .or_(page.get_by_label("Password"))
            .or_(page.locator("#login-password"))
        )
        username_field.first.fill(self.username)
        password_field.first.fill(self.password)

        submit_button = (
            page.locator("button[type='submit']")
            .or_(page.get_by_role("button", name="Login"))
            .or_(page.get_by_role("button", name="Log in"))
            .or_(page.get_by_role("button", name="Sign in"))
        )
        submit_button.first.click()

        # Give Rumble a moment to either land on the homepage or prompt 2FA.
        page.wait_for_timeout(3000)

        two_fa_field = page.locator("input[name*='2fa'], input[name*='code'], input[autocomplete='one-time-code']")
        if two_fa_field.count() > 0 and two_fa_field.first.is_visible():
            code = input("[Rumble] 2FA code requested - check your authenticator/email and enter it here: ")
            two_fa_field.first.fill(code)
            page.get_by_role("button", name="Verify").or_(page.get_by_role("button", name="Submit")).click()
            page.wait_for_timeout(3000)

    def _upload_video(
        self, page, video_path, title, description, tags, privacy, thumbnail_path, progress_callback
    ) -> str:
        page.goto(self.upload_url, timeout=60_000)

        # "Filedata" is Rumble's actual field id (confirmed via a
        # community open-source Rumble uploader); type-based fallback
        # first in case it's changed since, then this specific id.
        file_input = page.locator("input[type='file']").or_(page.locator("#Filedata")).first
        file_input.set_input_files(video_path)

        # Rumble starts processing/uploading immediately after file select;
        # wait for the metadata form (title field) to become available.
        title_field = page.get_by_label("Title").or_(page.locator("input[name='title']"))
        title_field.wait_for(state="visible", timeout=120_000)
        title_field.fill(title)

        description_field = page.get_by_label("Description").or_(page.locator("textarea[name='description']"))
        if description_field.count() > 0:
            description_field.fill(description)

        tags_field = page.get_by_label("Tags").or_(page.locator("input[name='tags']"))
        if tags_field.count() > 0:
            tags_field.fill(", ".join(t.lstrip("#") for t in tags))

        # Visibility: Public / Unlisted / Private radio buttons.
        if privacy.lower() == "public":
            visibility_option = page.get_by_label("Public").or_(page.get_by_text("Public", exact=True))
            if visibility_option.count() > 0:
                visibility_option.first.click()

        if thumbnail_path and os.path.exists(thumbnail_path):
            thumb_input = page.locator("input[type='file'][accept*='image']")
            if thumb_input.count() > 0:
                thumb_input.first.set_input_files(thumbnail_path)

        # Rumble's upload form has a required "I agree to terms" checkbox
        # (sometimes two) before the submit button will actually do
        # anything - check any that are present and unchecked.
        for checkbox in page.locator("input[type='checkbox']").all():
            try:
                if checkbox.is_visible() and not checkbox.is_checked():
                    checkbox.check()
            except Exception:
                continue  # a stray/detached checkbox shouldn't abort the whole upload

        # Poll Rumble's own upload-progress indicator if present, so we can
        # report percentage back the same way the YouTube uploader does.
        progress_bar = page.locator("[role='progressbar'], .progress-bar, .upload-progress")
        deadline = time.time() + 60 * 60  # generous ceiling for very large files
        last_reported = -1
        while time.time() < deadline:
            if progress_bar.count() > 0:
                value = progress_bar.first.get_attribute("aria-valuenow")
                if value and value.isdigit() and int(value) != last_reported:
                    last_reported = int(value)
                    if progress_callback:
                        progress_callback(last_reported)
                    if last_reported >= 100:
                        break

            submit_button = (
                page.get_by_role("button", name="Submit")
                .or_(page.get_by_role("button", name="Publish"))
                .or_(page.get_by_role("button", name="Upload"))
            )
            if submit_button.count() > 0 and submit_button.first.is_enabled():
                submit_button.first.click()
                break

            page.wait_for_timeout(2000)

        # After submit, Rumble usually redirects to the video's own page.
        try:
            page.wait_for_url("**/v*", timeout=120_000)
        except PlaywrightTimeoutError:
            pass

        if progress_callback:
            progress_callback(100)

        return page.url

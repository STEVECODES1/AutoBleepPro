"""
Rumble uploader via browser automation (Playwright).

IMPORTANT - read this before relying on it: Rumble has no public API for
regular creators to upload programmatically, unlike YouTube's official,
documented Data API. This module drives Rumble's own web upload form the
same way a human would, using Playwright. That means:

  - It needs your real Rumble username/password (from .env).
  - It's fragile: if Rumble redesigns their upload page, the locators
    below may stop matching and this will need updating.
  - If your account has 2FA enabled, this pauses and asks you to type the
    code into the terminal - it can't (and shouldn't try to) bypass 2FA.
  - Automated form-filling on a site that doesn't offer an API for it is
    a gray area against most platforms' terms of use for bot traffic;
    this runs as one interactive login per session (not a high-volume
    scraper), but you're responsible for how you use it.

If Rumble ever changes their page and a step below fails, the fastest fix
is: `playwright codegen https://rumble.com/upload.php` in a terminal,
manually click through an upload, and update the locator on the matching
line here from what codegen records.
"""

import os
import time
from typing import Callable, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


class RumbleUploader:
    def __init__(self, username: str, password: str, login_url: str, upload_url: str, headless: bool = False):
        self.username = username
        self.password = password
        self.login_url = login_url
        self.upload_url = upload_url
        # Headless=False by default so you can see what's happening (and
        # solve 2FA/captchas manually) the first few times you run this.
        self.headless = headless

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
        if not self.username or not self.password:
            raise RuntimeError("RUMBLE_USERNAME / RUMBLE_PASSWORD not set in .env")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            try:
                self._login(page)
                video_url = self._upload_video(
                    page, video_path, title, description, tags, privacy, thumbnail_path, progress_callback
                )
            finally:
                browser.close()

        return video_url

    def _login(self, page) -> None:
        page.goto(self.login_url, timeout=60_000)

        # Best-effort locators - update these first if login breaks.
        page.get_by_label("Username or Email").or_(page.locator("#login-username")).fill(self.username)
        page.get_by_label("Password").or_(page.locator("#login-password")).fill(self.password)
        page.get_by_role("button", name="Login").click()

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

        # File input is usually a hidden <input type="file">; Playwright
        # can set it directly without needing the native file picker dialog.
        file_input = page.locator("input[type='file']").first
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

            submit_button = page.get_by_role("button", name="Submit").or_(page.get_by_role("button", name="Publish"))
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

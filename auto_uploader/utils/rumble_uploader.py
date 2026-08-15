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
import re
import time
from typing import Callable, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# Recorded instead of a URL when the upload succeeded but Rumble never
# showed a link. Deliberately not "FAILED:" - the video IS up, so dedup
# must still treat it as done and not re-upload it on the next run.
UPLOADED_NO_URL = "uploaded (Rumble did not show a link - check your dashboard)"

# A published Rumble video is /v<id>-<slug>.html. Matching a bare "/v"
# prefix also matches the site's own /videos browse page, which is a link
# present on every upload page - that got recorded as the "video URL" and
# posted to Discord. The id must contain a digit and be followed by the
# slug separator, which no navigation path is.
_VIDEO_URL = re.compile(r"^https://rumble\.com/v(?=[a-z0-9]*\d)[a-z0-9]{3,}[-.]", re.I)


def _is_video_url(candidate: str) -> bool:
    if not candidate or "/upload" in candidate:
        return False
    return bool(_VIDEO_URL.match(candidate.strip()))



def _set_file_via_cdp(page, selector: str, file_path: str) -> bool:
    """Hand Chrome a local path instead of streaming the bytes to it.

    Playwright's set_input_files refuses anything over 50 MB when it is
    attached over CDP: it cannot tell that the browser is on this machine,
    so it assumes the file would have to cross a network. Stream recordings
    are gigabytes, so that ceiling blocks every real upload.

    DOM.setFileInputFiles is the CDP command underneath, and it takes a
    PATH that the browser opens itself. Chrome is local, so there is no
    transfer at all and no size limit.
    """
    try:
        cdp = page.context.new_cdp_session(page)
        document = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        found = cdp.send("DOM.querySelector", {
            "nodeId": document["root"]["nodeId"], "selector": selector})
        node_id = found.get("nodeId")
        if not node_id:
            return False
        cdp.send("DOM.setFileInputFiles", {
            "files": [os.path.abspath(file_path)], "nodeId": node_id})
        return True
    except Exception:
        return False


class RumbleUploader:
    def __init__(
        self,
        username: str,
        password: str,
        login_url: str,
        upload_url: str,
        headless: bool = False,
        cdp_url: Optional[str] = None,
        primary_category: str = "Gaming",
        secondary_category: str = "",
    ):
        self.username = username
        self.password = password
        self.login_url = login_url
        self.upload_url = upload_url
        self.primary_category = primary_category
        self.secondary_category = secondary_category
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
            browser = page = None
            should_close_browser = True
            attached = False

            # Attach to the user's own logged-in Chrome when it's there.
            if self.cdp_url:
                # Start one on the port if nothing is listening, rather
                # than dropping to the password path. That fallback opens
                # a fresh, logged-out browser and types credentials into
                # Rumble's login form - a worse route in every way, and it
                # looks from outside like the tool ignored the setting.
                from utils.chrome_cdp import ensure_chrome

                ready, detail = ensure_chrome(self.cdp_url)
                print(f"[Rumble] {detail}")
                try:
                    browser = p.chromium.connect_over_cdp(self.cdp_url)
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = context.new_page()
                    should_close_browser = False  # the user's window - don't close it
                    attached = True
                    print(f"[Rumble] Attached to Chrome at {self.cdp_url}.")
                except Exception as exc:
                    # Chrome isn't running with --remote-debugging-port. Falling
                    # back beats failing the upload: cdp_url is configured for
                    # the good path, not as a hard requirement.
                    print(f"[Rumble] Could not attach to Chrome at {self.cdp_url} ({exc}).")
                    if self.username and self.password:
                        print("[Rumble] Falling back to username/password login. "
                              "For the reliable path, launch Chrome with "
                              "--remote-debugging-port=9222 and log into Rumble there.")
                    browser = page = None

            if page is None:
                if not self.username or not self.password:
                    raise RuntimeError(
                        "Could not attach to Chrome at "
                        f"{self.cdp_url or '(unset)'}, and RUMBLE_USERNAME/"
                        "RUMBLE_PASSWORD are not set in .env either - so there "
                        "is no way to reach Rumble. Either launch Chrome with "
                        "--remote-debugging-port=9222 and log in, or fill in "
                        "the .env credentials."
                    )
                browser = p.chromium.launch(headless=self.headless)
                page = browser.new_page()
                should_close_browser = True

            try:
                if not attached:
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

    def _set_visibility(self, page, privacy: str) -> None:
        """Select the visibility radio (Public/Unlisted/Private).

        Rumble renders these as real <input type="radio"> elements that are
        visually hidden behind custom-styled labels, so a plain .click()
        blocks forever on "element is not visible". check(force=True) sets
        the underlying input instead of clicking pixels.

        This deliberately does NOT skip when the radio already reads as
        checked. It used to, on the assumption that Public is Rumble's
        default - but real uploads landed `visibility: private` while the
        log said "already set to public", so that assumption is wrong and
        the short-circuit was hiding it. Setting an already-correct radio
        costs nothing; not setting it cost a private upload.
        """
        wanted = (privacy or "public").strip().lower()
        radio = page.locator(f"input[type='radio'][name='visibility'][value='{wanted}']")

        self._describe_visibility(page)

        try:
            if radio.count() == 0:
                print(f"[Rumble] No visibility radio found for {wanted!r}; leaving Rumble's default.")
                return

            try:
                radio.first.check(force=True, timeout=15_000)
            except Exception:
                # force=True still performs a click, which fails outright on
                # a zero-size/offscreen input. Click the <label> that points
                # at it instead (what a real user actually clicks), and fall
                # back to setting the input directly if there's no label.
                element_id = radio.first.get_attribute("id")
                clicked = False
                if element_id:
                    label = page.locator(f"label[for='{element_id}']")
                    if label.count() > 0:
                        label.first.click(timeout=15_000)
                        clicked = True
                if not clicked:
                    radio.first.evaluate(
                        "el => { el.checked = true;"
                        " el.dispatchEvent(new Event('input', {bubbles:true}));"
                        " el.dispatchEvent(new Event('change', {bubbles:true})); }"
                    )

            if radio.first.is_checked():
                print(f"[Rumble] Visibility set to {wanted}.")
            else:
                print(f"[Rumble] WARNING: visibility {wanted!r} did not take; using Rumble's default.")
                self._describe_visibility(page)
        except Exception as exc:
            # Never fatal: Public is Rumble's default anyway, so failing to
            # touch this control shouldn't sink an otherwise-fine upload.
            print(f"[Rumble] WARNING: could not set visibility to {wanted!r} ({exc}); using Rumble's default.")

    # Only checkboxes whose label matches this are safe to tick automatically.
    # Blanket-checking every checkbox on Rumble's form would also enable
    # "Feature video on the top of your profile" and "Send mobile push
    # notification to followers" - the latter spams every follower, once per
    # uploaded video. Opt in explicitly, never by default.
    # "agreement" listed explicitly: \bagree\b does NOT match "agreement",
    # which caused a real run to skip Rumble's required rights attestation
    # ("You have not signed an exclusive agreement with any other parties").
    _TERMS_PATTERN = re.compile(
        r"\b(terms|conditions|agree|agreement|rights|licen[cs]e|authori[sz]ed|own|policy|exclusive)\b",
        re.I,
    )

    def _submit(self, page, title: str = "") -> Optional[str]:
        """Click through Rumble's submit step(s). Returns the video URL if
        Rumble reveals one, else None.

        Rumble's upload page has TWO forms: the video-details form, then a
        rights/terms form with its own final submit. Clicking only the
        first leaves the video sitting unpublished, so this clicks each
        submit control in turn (re-accepting terms in between, since the
        second form's checkboxes only exist once it's shown).

        Critically it STOPS as soon as the published link appears. It used
        to run a fixed three iterations, so after the real publish landed
        on click two, a third click hit whatever submit-ish control was
        left on the success page. One upload ended on rumble.com/videos -
        a navigation link, not a video - and that video never appeared on
        the channel at all.

        That early stop is only safe because the link is matched against
        `title`. Without it, any video link already on the page ends the
        loop after step 1 and the rights/terms form never gets filled -
        which is exactly how a VOD was reported as published under the
        address of an unrelated video, and never appeared at all.
        """
        submit_locator = (
            page.get_by_role("button", name=re.compile(r"^(submit|publish|upload)$", re.I))
            .or_(page.locator("input[type='submit']"))
        )

        clicked_any = False
        for step in range(3):  # details form, rights/terms form, +1 retry slot
            candidates = [c for c in submit_locator.all() if self._is_clickable(c)]
            if not candidates:
                break

            candidates[0].click(timeout=120_000)
            clicked_any = True
            print(f"[Rumble] Submit step {step + 1} clicked.")

            # Publication confirmed - stop here. Clicking again from the
            # success page is what navigated away in earlier runs.
            #
            # WAITED FOR, not glanced at. This was one fixed 3s pause,
            # which asks Rumble to finish inside three seconds after a
            # multi-GB upload. When it did not, the loop pressed submit a
            # SECOND time on a form that had already gone through - the
            # way one clip becomes two videos on the channel.
            published = self._await_published(page, title)
            if published:
                print(f"[Rumble] Published: {published}")
                return published

            # If Rumble rejected the submit over the missing required
            # category, fix it and let the loop click submit again.
            try:
                if page.get_by_text(re.compile(r"select at least one category", re.I)).count() > 0:
                    print("[Rumble] Category error after submit - retrying category selection.")
                    self._select_categories(page)
                    page.wait_for_timeout(800)
                    continue
            except Exception:
                pass

            # The rights/terms checkboxes usually only render on the second
            # form, so accept them after the first click rather than before.
            self._accept_terms(page)

        if not clicked_any:
            raise RuntimeError("No enabled submit/publish button found on the page.")
        return self._find_video_url(page, title)

    # How long to let Rumble finish publishing before concluding the
    # submit did not take. Generous on purpose: the cost of waiting too
    # long is a slow run, and the cost of not waiting long enough is a
    # duplicate video that has to be deleted by hand.
    PUBLISH_WAIT_SECONDS = 25
    PUBLISH_POLL_SECONDS = 1.5

    def _await_published(self, page, title: str):
        """Poll for the published link instead of glancing once."""
        deadline = time.time() + self.PUBLISH_WAIT_SECONDS
        while True:
            found = self._find_video_url(page, title)
            if found:
                return found
            if time.time() >= deadline:
                return None
            page.wait_for_timeout(int(self.PUBLISH_POLL_SECONDS * 1000))

    @staticmethod
    def _is_clickable(locator) -> bool:
        try:
            return locator.is_visible() and locator.is_enabled()
        except Exception:
            return False

    def _accept_terms(self, page) -> None:
        """Tick only the rights/terms-agreement checkboxes Rumble requires."""
        for checkbox in page.locator("input[type='checkbox']").all():
            try:
                if checkbox.is_checked():
                    continue

                label_text = ""
                element_id = checkbox.get_attribute("id")
                if element_id:
                    label = page.locator(f"label[for='{element_id}']")
                    if label.count() > 0:
                        label_text = label.first.inner_text() or ""
                if not label_text:
                    # Fall back to the checkbox's own enclosing-label text.
                    try:
                        label_text = checkbox.evaluate(
                            "el => (el.closest('label')?.innerText) || el.parentElement?.innerText || ''"
                        ) or ""
                    except Exception:
                        label_text = ""

                if not self._TERMS_PATTERN.search(label_text):
                    print(f"[Rumble] Leaving optional checkbox unticked: {label_text.strip()[:70]!r}")
                    continue

                self._tick_checkbox(checkbox, label_text)
            except Exception:
                continue  # a stray/detached checkbox shouldn't abort the upload

    def _tick_checkbox(self, checkbox, label_text: str) -> None:
        """Tick a required checkbox, loudly.

        Rumble's real markup (from the saved page dump) is a plain
        <input type=checkbox id=crights|cterms> hidden behind a styled
        <label><span>. check(force=True) still performs a physical click,
        which fails outright on a zero-size input - and the old code
        swallowed that failure silently, which is why a real run showed
        NOTHING about these checkboxes and the form then refused to
        submit ("You must agree..."). Fall back to a JS click on the
        input itself (native checkbox toggle + change event, works on
        hidden elements - and unlike clicking the label, can't
        accidentally hit the terms-of-service LINK inside cterms'
        label), then to setting .checked directly with proper events.
        """
        short = label_text.strip()[:70]
        try:
            checkbox.check(force=True, timeout=8_000)
        except Exception:
            try:
                checkbox.evaluate("el => el.click()")
            except Exception:
                pass
            try:
                if not checkbox.is_checked():
                    checkbox.evaluate(
                        "el => { el.checked = true;"
                        " el.dispatchEvent(new Event('input', {bubbles: true}));"
                        " el.dispatchEvent(new Event('change', {bubbles: true})); }"
                    )
            except Exception:
                pass

        try:
            if checkbox.is_checked():
                print(f"[Rumble] Accepted: {short!r}")
            else:
                print(f"[Rumble] WARNING: could not tick required checkbox: {short!r} "
                      "- tick it manually in the browser window before submitting.")
        except Exception:
            print(f"[Rumble] WARNING: could not verify checkbox state: {short!r}")

    def _dump_page(self, page) -> str:
        """Save the live page HTML next to this module for inspection."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs",
            f"rumble_page_dump_{int(time.time())}.html",
        )
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(page.content())
            return path
        except Exception:
            return "(could not write page dump)"

    def _describe_buttons(self, page) -> str:
        """Short summary of clickable things on the page, for error output."""
        described = []
        try:
            for loc in page.locator("button, input[type='submit'], input[type='button']").all()[:25]:
                try:
                    label = (loc.inner_text() or loc.get_attribute("value") or "").strip()
                    described.append(f"{label!r}{'' if loc.is_visible() else ' (hidden)'}")
                except Exception:
                    continue
        except Exception:
            pass
        return ", ".join(described) or "(none found)"

    def _describe_visibility(self, page) -> None:
        """Log every visibility control on the page and its current state.

        Uploads have come out private while the log claimed public, which
        means the control being read is not the one that governs. Printing
        the real set of radios (name, value, checked) on each run is what
        will identify the right one, rather than another guess.
        """
        try:
            found = page.evaluate(
                "() => Array.from(document.querySelectorAll('input[type=radio]'))"
                "  .map(el => ({name: el.name, value: el.value, checked: el.checked}))"
                "  .filter(r => /visib|privac|public|private|unlisted|draft|publish/i"
                "               .test((r.name || '') + ' ' + (r.value || '')))"
            )
        except Exception:
            return
        if not found:
            print("[Rumble] (no visibility-like radios on this step)")
            return
        for r in found:
            state = "CHECKED" if r.get("checked") else "unchecked"
            print(f"[Rumble]   radio name={r.get('name')!r} value={r.get('value')!r} {state}")

    def _select_categories(self, page) -> None:
        """Pick the primary (and secondary) category. REQUIRED by Rumble -
        the form refuses to submit ("Please select at least one category")
        until primary is set.

        Searches EVERY frame on the page, not just the top document: a real
        run showed the dropdowns clearly visible on screen while both the
        native-select and custom-widget lookups found nothing in the main
        document - the classic signature of the form living in an iframe,
        which page-level locators don't pierce.
        """
        # The category section can also render a beat LATER than the rest
        # of the form - so poll for up to 30s for Rumble's search-select
        # widget (identified from a saved page dump of the real form) or a
        # native <select> to exist in any frame before concluding absent.
        widget_frame = None
        deadline = time.time() + 30
        while time.time() < deadline and widget_frame is None:
            for frame in page.frames:
                try:
                    if frame.locator("input.select-search-input").count() > 0:
                        widget_frame = frame
                        break
                    if frame.locator("select").count() > 0:
                        self._select_native(frame.locator("select").all())
                        return
                except Exception:
                    continue
            if widget_frame is None:
                page.wait_for_timeout(1500)

        all_ok = True
        if widget_frame is not None:
            for which, desired in (("primary", self.primary_category),
                                   ("secondary", self.secondary_category)):
                if desired and not self._select_searchselect(widget_frame, which, desired):
                    all_ok = False
        else:
            # Unknown markup - fall back to the older text-based guesses.
            for placeholder, desired in (("Primary", self.primary_category),
                                         ("Secondary", self.secondary_category)):
                if desired and not self._select_custom_dropdown(page, placeholder, desired):
                    all_ok = False

        if not all_ok:
            # Selector inference has failed enough times - capture the real
            # markup so the next fix comes from data, and tell the user the
            # manual-rescue path (clicks in the visible window still count).
            dump = self._dump_page(page)
            print(
                f"[Rumble] Category selection failed - page HTML dumped to: {dump}\n"
                "[Rumble] You can pick the categories manually in the browser window "
                "right now; the upload will continue and submit normally."
            )

    def _select_searchselect(self, frame, which: str, desired: str) -> bool:
        """Drive Rumble's actual category widget. Markup, verbatim from a
        saved dump of the real upload page:

            <div class="select-container">
              <input id="category_primary" class="select-search-value" hidden>
              <input class="select-search-input" name="primary-category"
                     placeholder="- Primary category -">
              <div class="select-options-container" role="listbox">
                <div class="select-option" data-value="4" data-label="Gaming"
                     role="option">

        The placeholder lives in an ATTRIBUTE, not text content - which is
        why every earlier get_by_text-based attempt found nothing. The
        hidden select-search-value input holds the chosen option's
        data-value ("0" = nothing selected), so success is verifiable.
        `which` is "primary" or "secondary".
        """
        container = frame.locator(f"div.select-container:has(input[name='{which}-category'])")
        if container.count() == 0:
            print(f"[Rumble] WARNING: could not find the {which} category widget.")
            return False
        container = container.first

        pairs = container.locator("div.select-option").evaluate_all(
            "els => els.map(e => [e.dataset.value, e.dataset.label])"
        )
        want = desired.strip().lower()
        value = label = None
        # Exact label first, then "<desired> (" prefix, then substring -
        # in that order because "Grand Theft Auto V" is a PREFIX of
        # "Grand Theft Auto Vice City", so a bare substring pass could
        # land on the wrong game.
        for v, l in pairs:
            if (l or "").strip().lower() == want:
                value, label = v, l
                break
        if value is None:
            for v, l in pairs:
                if (l or "").strip().lower().startswith(want + " ("):
                    value, label = v, l
                    break
        if value is None:
            for v, l in pairs:
                if want in (l or "").strip().lower():
                    value, label = v, l
                    break
        if value is None:
            print(f"[Rumble] WARNING: no {which} category option matches {desired!r}.")
            return False

        # Real-user path: focus the search box, type to filter, click the
        # option. If the option isn't clickable (list closed/virtualized),
        # dispatch the click in JS - that still runs the widget's own
        # handler, which is what fills the hidden value input.
        try:
            search = container.locator("input.select-search-input").first
            search.click(timeout=8_000)
            search.fill(label)
            frame.wait_for_timeout(400)
            container.locator(f"div.select-option[data-value='{value}']").first.click(timeout=8_000)
        except Exception:
            container.evaluate(
                "(c, v) => { const o = c.querySelector(`div.select-option[data-value='${v}']`); if (o) o.click(); }",
                value,
            )

        chosen = container.locator("input.select-search-value").input_value()
        if not chosen or chosen == "0":
            # Last resort: set the hidden value + visible text directly and
            # fire the events the widget would have fired itself.
            container.evaluate(
                """(c, args) => {
                     const [v, l] = args;
                     for (const [sel, val] of [['input.select-search-value', v],
                                               ['input.select-search-input', l]]) {
                       const el = c.querySelector(sel);
                       if (el) {
                         el.value = val;
                         el.dispatchEvent(new Event('input', {bubbles: true}));
                         el.dispatchEvent(new Event('change', {bubbles: true}));
                       }
                     }
                   }""",
                [value, label],
            )
            chosen = container.locator("input.select-search-value").input_value()

        if chosen and chosen != "0":
            print(f"[Rumble] Category set: {label} ({which})")
            return True
        print(f"[Rumble] WARNING: could not set {which} category {desired!r}.")
        return False

    def _select_custom_dropdown(self, page, placeholder: str, desired: str) -> bool:
        """Open a custom (non-<select>) dropdown and pick a matching option.
        Returns True on success. Checks every frame, not just the page."""
        pattern = re.compile(rf"-\s*{placeholder}\s+category\s*-", re.I)

        control = None
        control_frame = None
        for frame in page.frames:
            try:
                candidate = frame.get_by_text(pattern)
                if candidate.count() > 0:
                    control = candidate.first
                    control_frame = frame
                    break
            except Exception:
                continue

        try:
            if control is None:
                print(f"[Rumble] WARNING: could not find the {placeholder} category dropdown.")
                return False
            page_or_frame = control_frame
            control.click(timeout=15_000)
            page.wait_for_timeout(600)  # let the option list render

            # Some of these widgets include a search box - typing narrows a
            # long list (Rumble's game list is huge) so the option is
            # actually rendered and clickable. Searched within the same
            # frame the control lives in.
            try:
                search = page_or_frame.locator("input[type='text']:visible, input[type='search']:visible").last
                if search.count() > 0:
                    search.fill(desired, timeout=5_000)
                    page.wait_for_timeout(800)
            except Exception:
                pass

            option = (
                page_or_frame.get_by_role("option", name=re.compile(re.escape(desired), re.I))
                .or_(page_or_frame.locator("li, [role='option'], .option, .dropdown-item")
                     .filter(has_text=re.compile(re.escape(desired), re.I)))
            )
            if option.count() == 0:
                print(f"[Rumble] WARNING: no option matching {desired!r} in the {placeholder} dropdown.")
                page.keyboard.press("Escape")
                return False

            option.first.click(timeout=15_000)
            print(f"[Rumble] Category set: {desired} ({placeholder})")
            page.wait_for_timeout(400)
            return True
        except Exception as exc:
            print(f"[Rumble] WARNING: could not set {placeholder} category {desired!r}: {exc}")
            return False

    def _select_native(self, selects) -> None:
        """Native <select> path (kept in case Rumble ever serves plain selects)."""
        wanted = [self.primary_category, self.secondary_category]

        for dropdown, desired in zip(selects, wanted):
            if not desired:
                continue
            try:
                # select_option matches on the visible label, which is what
                # the config holds (e.g. "Gaming") - Rumble's underlying
                # option values are opaque numeric ids.
                dropdown.select_option(label=desired, timeout=10_000)
                print(f"[Rumble] Category set: {desired}")
                continue
            except Exception:
                pass

            # Fall back to a case-insensitive / partial match against
            # whatever options this dropdown actually offers, so a
            # slightly-off config string doesn't silently block the upload.
            try:
                options = [o.strip() for o in dropdown.locator("option").all_text_contents()]
                match = next((o for o in options if o.lower() == desired.strip().lower()), None)
                if not match:
                    match = next((o for o in options if desired.strip().lower() in o.lower()), None)
                if match:
                    dropdown.select_option(label=match, timeout=10_000)
                    print(f"[Rumble] Category set: {match} (matched from config value {desired!r})")
                else:
                    # Loud, not silent: an unset REQUIRED category is
                    # precisely what leaves the submit button dead, and a
                    # silent failure here previously cost several runs.
                    print(
                        f"[Rumble] WARNING: category {desired!r} not found in dropdown. "
                        f"Available options: {options}"
                    )
            except Exception as exc:
                print(f"[Rumble] WARNING: could not set category {desired!r}: {exc}")

    def _wait_for_upload_complete(self, page, progress_callback, timeout_seconds: int = 60 * 90) -> None:
        """Block until Rumble's own progress readout reaches 100%.

        Rumble renders progress as page text ("3%", "(1.3MB/s - 27s)")
        rather than a standard aria-valuenow progressbar, so this scrapes
        the percentage out of the page text instead of reading an
        attribute. Returns (rather than raising) on timeout so the caller
        can still attempt the submit - a stalled readout isn't proof the
        upload failed.
        """
        deadline = time.time() + timeout_seconds
        last_reported = -1

        while time.time() < deadline:
            percent = None
            try:
                body_text = page.locator("body").inner_text(timeout=5_000)
                match = re.search(r"(\d{1,3})\s*%", body_text)
                if match:
                    percent = int(match.group(1))
            except Exception:
                pass

            if percent is not None and percent != last_reported:
                last_reported = percent
                if progress_callback:
                    progress_callback(percent)

            if percent is not None and percent >= 100:
                return

            # Rumble swaps the progress readout out for the finished state
            # once the transfer completes; treat "no percentage on the page
            # anymore, but we saw one earlier" as done too.
            if percent is None and last_reported > 0:
                return

            page.wait_for_timeout(3000)

    def _upload_video(
        self, page, video_path, title, description, tags, privacy, thumbnail_path, progress_callback
    ) -> str:
        page.goto(self.upload_url, timeout=60_000)

        # "Filedata" is Rumble's actual field id (confirmed via a
        # community open-source Rumble uploader); type-based fallback
        # first in case it's changed since, then this specific id.
        # CDP first: a stream recording is far past Playwright's 50 MB
        # ceiling for an attached browser, and that path fails outright.
        if not _set_file_via_cdp(page, "input[type='file'], #Filedata", video_path):
            file_input = page.locator("input[type='file']").or_(
                page.locator("#Filedata")).first
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

        self._set_visibility(page, privacy)

        if thumbnail_path and os.path.exists(thumbnail_path):
            thumb_input = page.locator("input[type='file'][accept*='image']")
            if thumb_input.count() > 0:
                # Thumbnails are small, so the plain path is fine here -
                # but go through CDP too when it works, for consistency.
                if not _set_file_via_cdp(
                        page, "input[type='file'][accept*='image']", thumbnail_path):
                    thumb_input.first.set_input_files(thumbnail_path)

        # CATEGORIES is a REQUIRED field on Rumble's upload form (marked
        # with a red asterisk) - without it the submit button never becomes
        # active, and the click loop below spins forever. Select it from
        # the primary/secondary category <select> dropdowns.
        self._select_categories(page)

        self._accept_terms(page)

        # Wait for the file transfer itself to finish BEFORE trying to
        # submit - Rumble shows a "N% (x MB/s)" indicator during upload and
        # the submit button isn't meaningfully clickable until it's done.
        # (Previously this raced the upload and burned ~60 futile click
        # retries against a still-uploading form.)
        self._wait_for_upload_complete(page, progress_callback)

        # Rumble shows "Please select at least one category" inline when
        # the required category didn't take. Catch that here with a clear
        # message rather than letting the submit click fail opaquely.
        try:
            if page.get_by_text(re.compile(r"select at least one category", re.I)).count() > 0:
                print("[Rumble] Category still unset - retrying category selection before submit.")
                self._select_categories(page)
                page.wait_for_timeout(800)
        except Exception:
            pass

        try:
            submitted_url = self._submit(page, title)
        except Exception as exc:
            # Dump the live page so the actual submit-button markup can be
            # inspected instead of guessed at - blind selector guessing has
            # cost several failed runs already.
            dump_path = self._dump_page(page)
            visible = self._describe_buttons(page)
            raise RuntimeError(
                "Rumble upload form never became submittable. "
                f"Page HTML saved to: {dump_path}\n"
                f"Buttons/inputs currently on the page: {visible}\n"
                "The video may still be uploaded on Rumble's side - check your Rumble "
                "account before retrying, and send the dumped HTML if this keeps happening."
            ) from exc

        # Rumble doesn't redirect after submit - it shows a "Direct Link"
        # box on the same upload.php page (observed in a real run, where
        # waiting for a redirect burned 120s per file and then recorded
        # upload.php as the "video URL"). Scrape the real link instead.
        direct_url = submitted_url
        deadline = time.time() + 45
        while time.time() < deadline and not direct_url:
            direct_url = self._find_video_url(page, title)
            if not direct_url:
                page.wait_for_timeout(2000)

        if progress_callback:
            progress_callback(100)

        if direct_url:
            return direct_url

        # The upload succeeded - never report the upload page itself as the
        # video URL. That string gets written into the dedup history and
        # posted to Discord, so a wrong-but-plausible link is worse than an
        # honest "couldn't read it": it looks like a working link and isn't.
        # Not always fatal - runs have ended here with the video still
        # published fine. But one run navigated away to rumble.com/videos
        # and the video never appeared at all, so this is worth checking
        # rather than reporting as a clean success.
        print("[Rumble] WARNING: Rumble never showed a video link. The upload has "
              "usually still landed, but one run ended here with no video "
              "created at all.")
        print("[Rumble]          Verify at https://rumble.com/account/content "
              "before assuming it published.")
        self._dump_page(page)
        return UPLOADED_NO_URL

    def _find_video_url(self, page, title: str = ""):
        """The published video's URL from the success page.

        Tried in order: the Direct Link input/textarea value, any anchor
        pointing at a video, then any rumble.com/v... link anywhere in the
        HTML. Anything under /upload is rejected - that's this page.

        `title` is what makes a candidate trustworthy. Rumble's upload
        page carries links to OTHER videos - sidebar, recommendations,
        the channel strip - and every one of them is a valid
        rumble.com/v... URL. Accepting any of them cost a full VOD: a
        stray link was read as the publish result after submit step 1,
        _submit stopped on it, the rights/terms form on step 2 was never
        filled, and the video stayed a draft that never appeared on the
        channel. The log said "uploaded successfully" with a link to
        somebody else's video.
        """
        candidates: list[str] = []

        try:
            candidates += [
                v for v in page.evaluate(
                    "() => Array.from(document.querySelectorAll('input,textarea'))"
                    "        .map(e => e.value || '')")
                if isinstance(v, str) and v.startswith("https://rumble.com/")
            ]
        except Exception:
            pass

        # Anchors, including root-relative hrefs ("/v6abc12-title.html").
        try:
            for href in page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]'))"
                "        .map(a => a.getAttribute('href') || '')"
            ):
                if not isinstance(href, str):
                    continue
                if href.startswith("/v"):
                    candidates.append("https://rumble.com" + href)
                elif href.startswith("https://rumble.com/v"):
                    candidates.append(href)
        except Exception:
            pass

        # Last resort: visible text only. Scraping raw page.content() also
        # matched URLs sitting inside <script> blocks, which is a false
        # positive with real consequences now that _submit stops as soon as
        # a link appears - it would stop before the video was published.
        try:
            candidates += re.findall(
                r"https://rumble\.com/v[A-Za-z0-9._-]+",
                page.evaluate("() => document.body ? document.body.innerText : ''"))
        except Exception:
            pass

        usable = [c.rstrip('\'"') for c in candidates]
        usable = [c for c in usable if _is_video_url(c)]
        if not usable:
            return None

        try:
            from .channel_vods import slug_matches_title
        except ImportError:
            from channel_vods import slug_matches_title

        matched = [c for c in usable if slug_matches_title(c, title) is True]
        if matched:
            return matched[0]

        # None judgeable means the title had too few distinctive words to
        # tell a real link from a sidebar one. Falling back to the first
        # link keeps the old behaviour for those, which is the best that
        # can be done without something to compare against.
        if title and slug_matches_title(usable[0], title) is not None:
            return None
        return usable[0]

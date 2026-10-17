"""One structured Discord post when a video has finished going out.

WHY THIS EXISTS
---------------
There were already two things posting to Discord and neither answered
the question anyone actually asks. `social_promoter.announce_upload` is
an ANNOUNCEMENT - it is written for followers and says "new video is
up", once, for a fresh upload. `record_stream` pings the same webhook
when a recording comes up short. Between them, nothing ever said what a
finished job actually did: which platforms took it, which refused,
which were skipped because the clip was already there, how long it
took, what got bleeped.

So the only way to know whether a night's work landed was to read two
console windows, and those windows scroll. A run that half-failed at
3am looked exactly like a run that worked.

This is the receipt. One post per finished video, every destination on
it, links where there are links and the reason where there are not.

WHAT MAKES IT A RECEIPT RATHER THAN AN ANNOUNCEMENT
---------------------------------------------------
It reports FAILURES as loudly as successes, so it is not something to
point followers at - it goes to the operator's own channel. It also
fires whether or not anything was newly uploaded, because "everything
was already posted, nothing to do" is a real and useful answer that the
announcement path deliberately stays silent about.

IT CANNOT BREAK A RUN
---------------------
Every entry point swallows its own errors and returns False. The video
is already published by the time this runs; a webhook that 404s must
never be the reason the pipeline reports failure.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

# Discord rejects an oversized payload with a 400 and the post silently
# does not appear, which is the worst way for this to fail - it is a
# reporting tool, so a report nobody receives is indistinguishable from
# a run that never happened. Everything below is trimmed to these.
MAX_TITLE = 256
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FIELDS = 25
MAX_DESCRIPTION = 4096

# Green, amber, red. Read at a glance from a phone notification, which
# is the whole point of sending it anywhere.
COLOUR_OK = 0x2ECC71
COLOUR_PARTIAL = 0xE67E22
COLOUR_FAILED = 0xE74C3C

_TIMEOUT = 15

# How a platform's recorded outcome reads. The uploader stores a URL on
# success and a "FAILED: ..." or "skipped: ..." string otherwise - see
# process_file - so the string itself is the status and there is no
# second source of truth to disagree with.
POSTED, SKIPPED, FAILED, PENDING = "posted", "skipped", "failed", "pending"

_MARK = {POSTED: "✅", SKIPPED: "⏭️",
         FAILED: "❌", PENDING: "⏳"}

# Shown instead of the raw key, because "zernio_tiktok" is an
# implementation detail and this is read by a person.
PLATFORM_NAMES = {
    "youtube": "YouTube",
    "rumble": "Rumble",
    "youtube_shorts": "YouTube Shorts",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "zernio_twitter": "X",
    "zernio_tiktok": "TikTok",
}


def webhook_url(explicit: str = "") -> str:
    """The receipt webhook, falling back to the general Discord one.

    A separate DISCORD_JOB_WEBHOOK_URL is supported because this is an
    operator's receipt and the other one may point at a public channel
    where "Rumble FAILED" is not something to broadcast. Falling back
    rather than requiring it keeps this working for anyone who has only
    ever set the one webhook.
    """
    return (str(explicit or "").strip()
            or os.environ.get("DISCORD_JOB_WEBHOOK_URL", "").strip()
            or os.environ.get("DISCORD_WEBHOOK_URL", "").strip())


def is_link(value: str) -> bool:
    return str(value or "").strip().lower().startswith(("http://", "https://"))


def classify(outcome: str) -> str:
    """What a recorded outcome string means."""
    text = str(outcome or "").strip()
    if not text:
        return PENDING
    if is_link(text):
        return POSTED
    lowered = text.lower()
    if lowered.startswith("failed") or "error" in lowered:
        return FAILED
    if lowered.startswith(("skip", "already")):
        return SKIPPED
    # Zernio answers a publishNow post without always returning a URL.
    # That is a success with no link, not an unknown - see
    # ZernioPublisher.post_clip.
    if "posted" in lowered:
        return POSTED
    return PENDING


def pretty(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform.replace("_", " ").title())


@dataclass
class JobReport:
    """Everything one finished video did, in one object."""

    title: str
    filename: str = ""
    # platform -> URL, "FAILED: ...", "skipped: ..." or ""
    destinations: dict = field(default_factory=dict)
    # Clips cut from this video, if it was a full stream.
    clips_made: int = 0
    clips_folder: str = ""
    # "13 slurs muted", or "" when nothing was censored.
    censor_note: str = ""
    seconds: float = 0.0
    is_clip: bool = False

    def statuses(self) -> dict:
        return {name: classify(value)
                for name, value in (self.destinations or {}).items()}

    def counts(self) -> dict:
        """How many destinations ended up in each column. Ones switched
        off in config are not part of the job and are not counted."""
        out = {POSTED: 0, PENDING: 0, HELD: 0, FAILED: 0, ALREADY: 0, SKIPPED: 0}
        for value in (self.destinations or {}).values():
            if is_off(value):
                continue
            k = kind(value)
            out[k] = out.get(k, 0) + 1
        return out

    def landed(self) -> int:
        return self.counts()[POSTED]

    def failed(self) -> int:
        return self.counts()[FAILED]

    def colour(self) -> int:
        if self.failed() and not self.landed():
            return COLOUR_FAILED
        if self.failed():
            return COLOUR_PARTIAL
        return COLOUR_OK

    def headline(self) -> str:
        """The one line that has to survive being read on a lock screen."""
        c = self.counts()
        if not any(c.values()):
            return "Nothing to post."
        if c[ALREADY] and not (c[POSTED] or c[PENDING] or c[HELD] or c[FAILED]):
            return "✅ Already up everywhere - nothing new to post."
        parts = []
        for key, word in ((POSTED, "posted"), (PENDING, "queued"),
                          (HELD, "held"), (FAILED, "failed")):
            if c[key]:
                parts.append(f"{c[key]} {word}")
        if not parts:
            return "Nothing new to post."
        mark = "❌" if c[FAILED] and not c[POSTED] else ("⚠️" if c[FAILED] else "✅")
        return f"{mark} " + " · ".join(parts)


# Destinations switched off in config are not part of the job. Listing
# them buried the one line that mattered: on 10-17 a clip's receipt said
# "Nothing landed - 1 of 14 failed" with eight "disabled in config" lines
# between the reader and the Rumble failure.
def is_off(outcome: str) -> bool:
    text = str(outcome or "").lower()
    return ("disabled in config" in text or " is disabled" in text
            or "not configured" in text)


HELD, ALREADY = "held", "already"


def kind(outcome: str) -> str:
    """classify(), split finer for the receipt's columns: held and
    already-up are their own, and a daily-cap or spacing skip is a wait."""
    text = str(outcome or "").strip()
    lowered = text.lower()
    status = classify(text)
    if status != POSTED and "held" in lowered:
        return HELD
    if status == SKIPPED:
        if "already" in lowered or "duplicate" in lowered:
            return ALREADY
        if any(w in lowered for w in ("cap", "spacing", "wait", "later",
                                      "queued", "deferred")):
            return PENDING
    return status


# A failure reason in plain words. The raw one is a stack of Playwright
# call log that nobody reads on a phone.
_PLAIN = (
    (("9222", "connect_over_cdp", "chrome is not reachable", "did not answer"),
     "browser froze - retried automatically"),
    (("quota",), "YouTube daily limit"),
    (("daily cap", "cap reached"), "daily limit reached"),
    (("slur",), "slur in the clip"),
    (("children", "kids", "child-safety"), "about kids"),
    (("gambling", "casino"), "gambling on screen"),
    (("timed out", "timeout"), "timed out"),
    (("401", "unauthor", "expired", "re-login", "reauth"), "needs a fresh login"),
)


def reason(outcome: str) -> str:
    text = str(outcome or "").strip()
    lowered = text.lower()
    for needles, plain in _PLAIN:
        if any(n in lowered for n in needles):
            return plain
    first = text.split("\n", 1)[0].strip()
    for prefix in ("FAILED:", "failed:", "skipped:", "held:"):
        if first.startswith(prefix):
            first = first[len(prefix):].strip()
    return _clip(first, 70)


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit - 1] + "…"


_COLUMNS = ((POSTED, "✅ Posted"), (PENDING, "⏳ Queued"), (HELD, "🚫 Held"),
            (FAILED, "❌ Failed"), (SKIPPED, "⏭️ Skipped"))


def build_embed(report: JobReport) -> dict:
    """The Discord embed for one finished job: every link together at
    the top, then one column per outcome, side by side."""
    columns = {key: [] for key, _ in _COLUMNS}
    links = []
    for platform, value in sorted((report.destinations or {}).items()):
        if is_off(value):
            continue
        name, text, k = pretty(platform), str(value or "").strip(), kind(value)
        if k == POSTED:
            if is_link(text):
                links.append(f"[{name}]({text})")
            columns[POSTED].append(name)
        elif k == ALREADY:
            url = next((w for w in text.split() if is_link(w)), "")
            if url:
                links.append(f"[{name}]({url})")
            columns[POSTED].append(f"{name} (already up)")
        elif k == PENDING:
            why = reason(text) if text else ""
            columns[PENDING].append(name + (f" - {why}" if why else ""))
        else:
            columns[k if k in columns else SKIPPED].append(f"{name} - {reason(text)}")

    description = ("🔗 " + "  •  ".join(links)) if links else "Nothing has posted yet."
    fields = [{"name": title, "value": _clip("\n".join(columns[key]), MAX_FIELD_VALUE),
               "inline": True}
              for key, title in _COLUMNS if columns[key]]

    took = ""
    if report.seconds:
        minutes, seconds = divmod(int(report.seconds), 60)
        took = f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"
    footer = " • ".join(p for p in (
        "Clip" if report.is_clip else "Stream",
        f"took {took}" if took else "",
        f"{report.clips_made} clips cut" if report.clips_made else "",
        report.censor_note,
        report.filename) if p)

    embed = {
        "title": _clip(report.title or report.filename or "Untitled", MAX_TITLE),
        "description": _clip(description, MAX_DESCRIPTION),
        "color": report.colour(),
        "fields": fields[:MAX_FIELDS],
        "footer": {"text": _clip(footer, 2048)},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
    }
    first = next((l.split("](", 1)[1][:-1] for l in links), "")
    if first:
        embed["url"] = first           # the title opens the first link
    return embed


def payload_for(report: JobReport) -> dict:
    return {"username": "AutoBleep",
            "content": report.headline(),
            "embeds": [build_embed(report)]}


def send(report: JobReport, webhook: str = "", post=None) -> bool:
    """Post the receipt. False on anything at all going wrong.

    `post` is injectable so the tests never touch the network.
    """
    url = webhook_url(webhook)
    if not url:
        return False
    body = json.dumps(payload_for(report)).encode("utf-8")
    if post is not None:
        try:
            post(url, body)
            return True
        except Exception:
            return False
    request = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "AutoBleep"})
    try:
        urllib.request.urlopen(request, timeout=_TIMEOUT)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError):
        return False


def report_job(cfg, report: JobReport, say=print) -> bool:
    """Send it if it is switched on. Never raises.

    Reads features.job_report, defaulting ON: somebody who has set a
    Discord webhook at all wants to know what happened, and the cost of
    an unwanted post is one message they can turn off.
    """
    try:
        features = dict(getattr(cfg, "features", {}) or {})
        setting = features.get("job_report", {})
        if isinstance(setting, dict):
            enabled = bool(setting.get("enabled", True))
            explicit = str(setting.get("webhook_url", "") or "")
        else:
            enabled, explicit = bool(setting), ""
        if not enabled:
            return False
        if not webhook_url(explicit):
            return False
        sent = send(report, explicit)
        if sent:
            say(f"[Report] Posted the receipt to Discord: {report.headline()}")
        return sent
    except Exception:
        # A receipt is the least important thing here and must never be
        # the reason a published video reports failure.
        return False

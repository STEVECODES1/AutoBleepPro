"""
What got posted, how it did, and what that says about the next one.

WHY THIS EXISTS
---------------
Every clip so far was chosen by a model reading a transcript and by a
scorer counting keywords, and neither of them has ever been told whether
a single clip worked. The pipeline has no memory: the twentieth stream is
picked exactly the way the first one was.

This is the memory. Three separate jobs, deliberately kept apart:

  remember()  at cut time - the FEATURES of a clip and where it was
              posted. Written once, never revised.
  harvest()   later - how many views each posted clip got. The only step
              that touches the network.
  learn()     any time - what the numbers say, as plain sentences.

WHAT IT WILL NOT DO
-------------------
It will not act on a hunch. `learn()` returns nothing at all until there
are MINIMUM_CLIPS clips with real view counts, and each finding needs
MINIMUM_PER_BAND clips on both sides of the comparison. A pipeline that
retunes itself on four data points is not learning, it is drifting, and
it would be indistinguishable from a bug that quietly stopped picking
good clips.

Findings are advisory and bounded. `Guidance.nudge()` moves a scorer's
duration preference by a capped amount; it cannot invert a setting or
push one past its limits. Everything else the guidance knows is handed
to the model as SENTENCES in the prompt, where a wrong lesson shows up
as a strange instruction a person can read, rather than as a weight
nobody can see.

Views are a weak signal and this module says so. A clip posted at 3am
against one posted at 6pm is not a fair comparison, and nothing here
pretends otherwise - which is why every finding carries the sample size
it came from, and why the report prints it.

NO CHAT, NO VIEWERS, NO PEOPLE
------------------------------
Records hold timings, durations, scores and view counts. The hook line
is stored because it is the streamer's own words on their own clip. No
chat, no usernames, no viewer data ever reaches this file.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

LEDGER_NAME = "clip_memory.json"

# Below this there is nothing to learn, and saying something anyway is
# worse than saying nothing - it would look like knowledge.
MINIMUM_CLIPS = 12

# A comparison needs both sides populated. Four clips in a band is thin,
# but it is a band, not a single clip that happened to go viral.
MINIMUM_PER_BAND = 4

# A finding must clear this much difference from the median to be worth
# printing. Under it, the two groups are the same group.
MINIMUM_LIFT = 0.20

# How far a lesson may move the scorer's duration preference, in seconds,
# however lopsided the numbers look. The guard rail is the point: a bad
# month of data must not be able to retune the pipeline into uselessness.
MAX_NUDGE_SECONDS = 6.0

# Records older than this stop counting toward findings. What worked in
# March is not evidence about August, and an audience changes.
STALE_AFTER_DAYS = 120


def _now() -> float:
    return time.time()


def clip_id(source: str, start: float) -> str:
    """Stable identity for a clip: which stream, and where in it.

    Deliberately not the filename. Filenames get renamed by hand, and the
    same clip re-cut after a fix must land on the same record rather than
    becoming a second one that double-counts.
    """
    return f"{(source or '').strip().lower()}@{start:.1f}"


@dataclass
class ClipRecord:
    """One clip: what it was, where it went, how it did."""

    clip_id: str
    source: str = ""
    start: float = 0.0
    duration: float = 0.0
    # Where in the stream it came from, 0.0-1.0. Streams have a shape -
    # the first twenty minutes are setup - and this is what lets that
    # show up in the numbers instead of staying a hunch.
    position: float = 0.0
    score: float = 0.0
    # "model", "vision" or "scorer" - which of the three picked it.
    picked_by: str = ""
    hook: str = ""
    profile: str = ""
    created: float = field(default_factory=_now)
    # platform -> url
    posted: dict = field(default_factory=dict)
    # platform -> view count
    views: dict = field(default_factory=dict)
    checked: float = 0.0

    def best_views(self) -> Optional[int]:
        """The most views this clip got anywhere, or None if never checked.

        The maximum rather than the sum: the same clip on four platforms
        is one clip that worked, and adding them up would rank a clip
        posted widely above a clip people actually watched.
        """
        numbers = [v for v in self.views.values() if isinstance(v, int) and v >= 0]
        return max(numbers) if numbers else None

    def hook_words(self) -> int:
        return len([w for w in (self.hook or "").split() if w])

    def is_question(self) -> bool:
        return "?" in (self.hook or "")

    def age_days(self) -> float:
        return (_now() - self.created) / 86400.0


class Ledger:
    """The file. Loads, saves, and never loses a record it did not write."""

    def __init__(self, path: str):
        self.path = path
        self._records: dict = {}
        self.load()

    # ── file ─────────────────────────────────────────────────────────

    def load(self) -> None:
        self._records = {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return
        for item in raw.get("clips", []) if isinstance(raw, dict) else []:
            try:
                record = ClipRecord(**item)
            except TypeError:
                # A record written by a newer version with a field this
                # one does not know. Skipping it is right; crashing on it
                # would take the whole upload run down over a log file.
                continue
            self._records[record.clip_id] = record

    def save(self) -> None:
        folder = os.path.dirname(os.path.abspath(self.path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        payload = {"version": 1,
                   "clips": [asdict(r) for r in self._records.values()]}
        # Written beside and moved into place, so an interrupted write
        # cannot leave a half-file where the history used to be.
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)
        os.replace(temporary, self.path)

    # ── writing ──────────────────────────────────────────────────────

    def remember(self, record: ClipRecord) -> ClipRecord:
        """Record a clip, or fill in what is missing on one already known.

        Re-cutting a clip must not create a second record - see clip_id.
        Existing view counts survive: they are the one thing here that
        cannot be recreated.
        """
        existing = self._records.get(record.clip_id)
        if existing is None:
            self._records[record.clip_id] = record
            return record
        for name, value in asdict(record).items():
            if name in ("views", "checked", "created", "clip_id"):
                continue
            if name == "posted":
                existing.posted.update(value or {})
                continue
            if value not in (None, "", 0, 0.0, {}):
                setattr(existing, name, value)
        return existing

    def note_post(self, key: str, platform: str, url: str) -> None:
        record = self._records.get(key)
        if record is not None and url:
            record.posted[platform] = url

    def note_views(self, key: str, platform: str, count: int) -> None:
        record = self._records.get(key)
        if record is not None and isinstance(count, int) and count >= 0:
            record.views[platform] = count
            record.checked = _now()

    # ── reading ──────────────────────────────────────────────────────

    def records(self) -> list:
        return list(self._records.values())

    def get(self, key: str) -> Optional[ClipRecord]:
        return self._records.get(key)

    def scored(self) -> list:
        """Records that can teach something: recent, and actually measured."""
        return [r for r in self._records.values()
                if r.best_views() is not None and r.age_days() <= STALE_AFTER_DAYS]

    def unchecked(self) -> list:
        """Posted somewhere, never counted. What harvest() should ask about."""
        return [r for r in self._records.values()
                if r.posted and r.best_views() is None]


# ── harvesting ───────────────────────────────────────────────────────

def views_for(url: str, timeout: int = 60) -> Optional[int]:
    """View count for one posted clip, or None.

    Reads the public metadata of a page the account owns. Returns None on
    anything unexpected rather than raising - a view count is the least
    important thing in this project and must never take a run down.
    """
    if not url:
        return None
    try:
        from auto_uploader.utils.clip_finder import ytdlp_command
    except Exception:
        try:
            from utils.clip_finder import ytdlp_command
        except Exception:
            def ytdlp_command():
                return ["yt-dlp"]
    args = ytdlp_command() + ["--dump-single-json", "--no-warnings",
                              "--skip-download", "--socket-timeout", "30", url]
    try:
        done = subprocess.run(args, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if done.returncode != 0:
        return None
    try:
        payload = json.loads(done.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None
    count = payload.get("view_count")
    return int(count) if isinstance(count, (int, float)) else None


def harvest(ledger: Ledger, limit: int = 40, say=None) -> int:
    """Fill in view counts for clips that have none. Returns how many.

    Bounded by `limit` because this is the only part that touches the
    network, and a ledger a year old should not turn one command into
    four hundred requests.
    """
    say = say or (lambda message: None)
    filled = 0
    for record in ledger.unchecked()[:limit]:
        for platform, url in sorted(record.posted.items()):
            count = views_for(url)
            if count is None:
                continue
            ledger.note_views(record.clip_id, platform, count)
            filled += 1
            say(f"  {platform:<14} {count:>7,}  {record.hook[:44]}")
    if filled:
        ledger.save()
    return filled


# ── learning ─────────────────────────────────────────────────────────

@dataclass
class Finding:
    """One thing the numbers say, in words, with the sample behind it."""

    subject: str
    better: str
    worse: str
    lift: float
    sample: int

    def sentence(self) -> str:
        return (f"{self.subject}: {self.better} beat {self.worse} by "
                f"{self.lift * 100:.0f}% ({self.sample} clips).")


@dataclass
class Guidance:
    """What has been learned. Empty is a valid, common and honest answer."""

    findings: list = field(default_factory=list)
    sample: int = 0
    median_views: float = 0.0
    best_duration: Optional[float] = None

    def __bool__(self) -> bool:
        return bool(self.findings)

    def summary(self) -> str:
        if not self.findings:
            if self.sample < MINIMUM_CLIPS:
                return (f"Not enough measured clips yet "
                        f"({self.sample}/{MINIMUM_CLIPS}). Nothing learned, "
                        f"nothing changed.")
            return (f"{self.sample} measured clips, and no pattern strong "
                    f"enough to act on. Nothing changed.")
        lines = [f"From {self.sample} measured clips "
                 f"(median {self.median_views:.0f} views):"]
        lines += [f"  - {f.sentence()}" for f in self.findings]
        return "\n".join(lines)

    def prompt_lines(self) -> list:
        """The lessons, as instructions for the model that picks clips.

        Sentences rather than weights on purpose: a wrong lesson shows up
        here as a strange instruction a person can read and delete, not as
        a number nobody can see.
        """
        if not self.findings:
            return []
        lines = ["What has actually worked on this channel so far "
                 f"(measured over {self.sample} posted clips):"]
        lines += [f"- {f.sentence()}" for f in self.findings]
        lines.append("Treat this as a tendency, not a rule. A genuinely "
                     "funny moment outside these patterns is still the "
                     "better clip.")
        return lines

    def nudge(self, low: float, high: float) -> tuple:
        """Move a duration preference toward what worked, within limits.

        Returns (low, high). Capped by MAX_NUDGE_SECONDS and clamped so
        the window can never invert or collapse - a bad month of data must
        not be able to retune the pipeline into uselessness.
        """
        if self.best_duration is None:
            return (low, high)
        middle = (low + high) / 2.0
        shift = max(-MAX_NUDGE_SECONDS,
                    min(MAX_NUDGE_SECONDS, self.best_duration - middle))
        new_low = max(1.0, low + shift)
        new_high = max(new_low + 5.0, high + shift)
        return (new_low, new_high)


def _split(records: list, key, threshold) -> tuple:
    low = [r for r in records if key(r) < threshold]
    high = [r for r in records if key(r) >= threshold]
    return low, high


def _lift(better: list, worse: list) -> Optional[float]:
    """How much more the better group got, as a fraction. None if unusable."""
    if len(better) < MINIMUM_PER_BAND or len(worse) < MINIMUM_PER_BAND:
        return None
    top = statistics.median([r.best_views() for r in better])
    bottom = statistics.median([r.best_views() for r in worse])
    if bottom <= 0:
        return None
    return (top - bottom) / bottom


def _compare(records, key, threshold, subject, high_label, low_label):
    """One comparison, in whichever direction the numbers actually go."""
    low, high = _split(records, key, threshold)
    for better, worse, better_name, worse_name in (
            (high, low, high_label, low_label),
            (low, high, low_label, high_label)):
        lift = _lift(better, worse)
        if lift is not None and lift >= MINIMUM_LIFT:
            return Finding(subject, better_name, worse_name, lift,
                           len(better) + len(worse))
    return None


def learn(ledger: Ledger) -> Guidance:
    """What the ledger says. Silence when it does not say anything."""
    records = ledger.scored()
    guidance = Guidance(sample=len(records))
    if len(records) < MINIMUM_CLIPS:
        return guidance

    counts = [r.best_views() for r in records]
    guidance.median_views = float(statistics.median(counts))

    durations = [r.duration for r in records if r.duration > 0]
    if durations:
        cut = statistics.median(durations)
        finding = _compare(records, lambda r: r.duration, cut,
                           "Length", f"clips over {cut:.0f}s",
                           f"clips under {cut:.0f}s")
        if finding:
            guidance.findings.append(finding)
            winners = [r.duration for r in records
                       if r.best_views() >= guidance.median_views
                       and r.duration > 0]
            if len(winners) >= MINIMUM_PER_BAND:
                guidance.best_duration = float(statistics.median(winners))

    finding = _compare(records, lambda r: r.position, 0.5, "Placement",
                       "the back half of the stream",
                       "the front half of the stream")
    if finding:
        guidance.findings.append(finding)

    finding = _compare(records, lambda r: float(r.hook_words()), 7.0,
                       "Titles", "longer titles", "titles under 7 words")
    if finding:
        guidance.findings.append(finding)

    finding = _compare(records, lambda r: 1.0 if r.is_question() else 0.0,
                       1.0, "Titles", "questions", "statements")
    if finding:
        guidance.findings.append(finding)

    for name in sorted({r.picked_by for r in records if r.picked_by}):
        finding = _compare(records,
                           lambda r, n=name: 1.0 if r.picked_by == n else 0.0,
                           1.0, "Chosen by", name, f"not {name}")
        if finding:
            guidance.findings.append(finding)
            break

    return guidance

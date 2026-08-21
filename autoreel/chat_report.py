"""Did chat actually change which moments got picked?

The chat signal was wired in without a single clip having been produced
by it. This answers the only question that matters about that change -
whether the audience's reaction moves the selection at all - by running
the SAME scorer twice over the same transcript, once blind and once with
chat, and diffing the two.

Nothing here changes how clips are chosen. It reports.

The important half is the second table: moments chat went off for that
the scorer did NOT pick. If chat is loud somewhere and the scorer has it
ranked sixtieth, either the transcript at that moment is genuinely dull -
a reaction to something visual, which the words cannot see - or the
weighting is too timid. Both are worth knowing and neither shows up in a
list of what was chosen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Laughter is NOT measured. There is no laughter detector in this
# project, and a column of zeroes labelled "laughter" would read as "no
# laughter here" rather than "nobody looked". Every row says so.
LAUGHTER = "not measured"


@dataclass
class Pick:
    """One selected window, and what the two runs thought of it."""
    start: float
    end: float
    text: str = ""
    score_blind: float = 0.0
    score_chat: float = 0.0
    rank_blind: Optional[int] = None   # None = not selected at all
    rank_chat: Optional[int] = None
    messages: int = 0
    spike: float = 0.0
    loudness: float = 0.0              # dB above this stream's median
    laughter: str = LAUGHTER

    @property
    def seconds(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def per_second(self) -> float:
        return self.messages / self.seconds if self.seconds else 0.0

    @property
    def movement(self) -> str:
        """What chat did to this window's standing."""
        if self.rank_chat is None:
            return "dropped"
        if self.rank_blind is None:
            return "NEW - chat put it in"
        if self.rank_chat < self.rank_blind:
            return f"up from #{self.rank_blind}"
        if self.rank_chat > self.rank_blind:
            return f"down from #{self.rank_blind}"
        return "unchanged"

    @property
    def climb(self) -> int:
        """How many places chat moved it up. Negative is down."""
        if self.rank_chat is None:
            return 0
        if self.rank_blind is None:
            return 999          # chat put it in from nowhere
        return self.rank_blind - self.rank_chat

    @property
    def why(self) -> str:
        """Plain reason this window is where it is."""
        if self.spike >= 2.0 and self.rank_chat is not None:
            return f"chat {self.spike:.1f}x its normal rate"
        if self.rank_chat is not None and self.rank_blind is not None \
                and self.rank_chat == self.rank_blind:
            return "the words alone; chat did not move it"
        if self.spike > 1.0:
            return f"words, nudged by chat ({self.spike:.1f}x)"
        return "the words alone; chat was quiet here"


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _messages_in(rates, start: float, end: float) -> int:
    from .chat_energy import WINDOW_SECONDS

    if not rates:
        return 0
    first = max(0, int(start // WINDOW_SECONDS))
    last = min(len(rates), int(end // WINDOW_SECONDS) + 1)
    return sum(rates[first:last])


def _rank_map(highlights) -> dict:
    """{(start, end): rank}, 1-based, best first."""
    ordered = sorted(highlights, key=lambda h: h.score, reverse=True)
    return {(round(h.start, 1), round(h.end, 1)): index
            for index, h in enumerate(ordered, start=1)}


def compare(segments, rates, count: int = 10, scorer_for=None,
            min_gap: float = 180.0, levels=None) -> tuple:
    """[Pick] for the windows chat selected, richest information first.

    Runs the scorer twice - blind and with chat - so the difference is
    attributable to chat and nothing else.
    """
    from .highlights import HighlightScorer
    from .chat_energy import spike_over

    def build(chat):
        maker = scorer_for or HighlightScorer
        return maker(chat=list(chat or []))

    listed = list(segments or ())
    blind = build([]).select_clips(listed, count=count * 6, min_gap=min_gap)
    withchat = build(rates).select_clips(listed, count=count * 6,
                                         min_gap=min_gap)
    blind_rank = _rank_map(blind)
    chat_rank = _rank_map(withchat)
    blind_score = {(round(h.start, 1), round(h.end, 1)): h.score for h in blind}

    chat_score = {(round(h.start, 1), round(h.end, 1)): h.score
                  for h in withchat}

    def make(highlight, ranks_from_blind: bool):
        key = (round(highlight.start, 1), round(highlight.end, 1))
        return Pick(
            start=highlight.start, end=highlight.end,
            text=highlight.hook or highlight.text,
            score_blind=blind_score.get(key, 0.0),
            score_chat=chat_score.get(key, 0.0),
            rank_blind=blind_rank.get(key),
            rank_chat=chat_rank.get(key),
            messages=_messages_in(rates, highlight.start, highlight.end),
            spike=spike_over(rates, highlight.start, highlight.end),
            loudness=_loudness(levels, highlight.start, highlight.end),
        )

    with_chat = [make(h, False) for h in
                 sorted(withchat, key=lambda h: h.score, reverse=True)[:count]]
    without = [make(h, True) for h in
               sorted(blind, key=lambda h: h.score, reverse=True)[:count]]
    return with_chat, without


def _loudness(levels, start: float, end: float) -> float:
    if not levels:
        return 0.0
    try:
        from .audio_energy import loudness_over

        return float(loudness_over(levels, start, end))
    except Exception:
        return 0.0


def biggest_climbers(with_chat: list, count: int = 5) -> list:
    """Where chat made the most difference, most first."""
    moved = [p for p in with_chat if p.climb > 0]
    return sorted(moved, key=lambda p: p.climb, reverse=True)[:count]


def chosen_without_chat(with_chat: list, quiet_below: float = 1.2,
                        count: int = 5) -> list:
    """Selected while chat was doing nothing.

    These are the ones the words carried on their own. If they turn out
    to be the good clips, chat is not the missing piece.
    """
    quiet = [p for p in with_chat if p.spike < quiet_below]
    return sorted(quiet, key=lambda p: p.score_chat, reverse=True)[:count]


def verdict(with_chat: list, without: list, rates) -> str:
    """Did chat improve anything? Said plainly, including "no"."""
    if not rates:
        return ("No chat was available, so this run tells you nothing "
                "about chat. The selections are the blind ones.")
    if not with_chat:
        return "Nothing was selected, so there is nothing to compare."

    same = {(round(p.start, 1), round(p.end, 1)) for p in without}
    changed = [p for p in with_chat
               if (round(p.start, 1), round(p.end, 1)) not in same]
    moved = [p for p in with_chat if p.climb not in (0,)]

    if not changed and not moved:
        return ("Chat changed NOTHING - the same clips in the same order. "
                "Either it is too quiet on this stream, or the weighting "
                "is too small to matter. Nothing here argues for keeping "
                "it on.")
    lines = [f"Chat replaced {len(changed)} of {len(with_chat)} selections "
             f"and reordered {len(moved)}."]
    lifted = biggest_climbers(with_chat, count=1)
    if lifted:
        best = lifted[0]
        where = "in from nowhere" if best.rank_blind is None             else f"from #{best.rank_blind} to #{best.rank_chat}"
        lines.append(f"The biggest move: {_clock(best.start)} came {where} "
                     f"on a {best.spike:.1f}x chat spike.")
    lines.append("Whether those are BETTER clips is a question for the "
                 "clips, not for this report. It measures what changed.")
    return " ".join(lines)


def loudest_rejected(segments, rates, picks, count: int = 5,
                     window: float = 30.0, levels=None) -> list:
    """[Pick] for the biggest chat spikes that did NOT get selected.

    The half that shows whether the scoring is working. A moment chat
    went off for and the scorer ignored is either a reaction to something
    on SCREEN - which the transcript cannot see - or a weighting that is
    too timid to matter.
    """
    from .chat_energy import WINDOW_SECONDS, spike_over

    if not rates:
        return []
    taken = [(p.start, p.end) for p in picks]

    def already(at: float) -> bool:
        return any(start - window <= at <= end + window
                   for start, end in taken)

    # The busiest seconds, most extreme first.
    moments = sorted(range(len(rates)), key=lambda i: rates[i], reverse=True)
    found, seen = [], []
    for index in moments:
        at = index * WINDOW_SECONDS
        if rates[index] <= 0 or already(at):
            continue
        if any(abs(at - other) < window * 2 for other in seen):
            continue
        seen.append(at)
        start = max(0.0, at - window / 2)
        end = start + window
        found.append(Pick(
            start=start, end=end,
            text=_said_between(segments, start, end),
            messages=_messages_in(rates, start, end),
            spike=spike_over(rates, start, end),
            loudness=_loudness(levels, start, end),
        ))
        if len(found) >= count:
            break
    return found


def _said_between(segments, start: float, end: float, limit: int = 90) -> str:
    words = []
    for segment in segments or ():
        try:
            if float(segment.get("end", 0)) < start:
                continue
            if float(segment.get("start", 0)) > end:
                break
        except (TypeError, ValueError):
            continue
        words.append(str(segment.get("text", "")).strip())
    said = " ".join(" ".join(words).split())
    return (said[:limit] + "…") if len(said) > limit else said


def render(with_chat, without, rejected, rates, name: str = "",
           levels=None) -> str:
    """The whole report, as text.

    Six sections, in the order somebody actually reads them: what was
    chosen, what would have been chosen blind, what chat moved, what chat
    wanted and did not get, what got in without chat's help, and then the
    verdict.
    """
    lines = ["=" * 100,
             f"CHAT vs NO CHAT   {name}".rstrip(),
             "=" * 100]

    if not rates:
        lines += [
            "",
            "NO CHAT WAS AVAILABLE for this video.",
            "",
            "Nothing below is a comparison - both columns would be the same",
            "run. Either the stream has no chat replay, or it was recorded",
            "before the source URL was written down beside it (see",
            "record_stream.remember_source), or the platform serves no",
            "replay at all.",
            "",
            "The clips it would cut are unaffected: chat only ever adjusts",
            "windows the words already put forward.",
            "=" * 100]
        return "\n".join(lines)

    lines += [
        f"{sum(rates):,} messages over {_clock(len(rates))}, "
        f"busiest second {max(rates)}/s",
        f"Audio energy: {'measured' if levels else 'not measured'}   "
        f"Laughter: {LAUGHTER} (no detector exists yet - the column would "
        f"read as 'no laughter' rather than 'nobody looked')",
    ]

    lines += _table("1. TOP WITH CHAT ENABLED", with_chat)
    lines += _table("2. TOP WITH CHAT DISABLED", without, blind=True)

    lines += ["", "3. WHERE CHAT MADE THE BIGGEST DIFFERENCE", ""]
    climbers = biggest_climbers(with_chat)
    if climbers:
        for pick in climbers:
            where = ("in from outside the top list" if pick.rank_blind is None
                     else f"#{pick.rank_blind} -> #{pick.rank_chat}")
            lines.append(f"  {_clock(pick.start):>8}  {where:<32} "
                         f"chat {pick.spike:.1f}x   {pick.text[:44]}")
    else:
        lines.append("  (none - chat moved nothing up)")

    lines += ["", "4. BIG CHAT SPIKES THAT WERE NOT SELECTED", "",
              f"  {'at':>8}  {'msg/s':>6}  {'spike':>7}  {'dB':>5}  "
              f"what was said"]
    for pick in rejected:
        lines.append(
            f"  {_clock(pick.start):>8}  {pick.per_second:>6.1f}  "
            f"{pick.spike:>6.1f}x  {pick.loudness:>5.1f}  "
            f"{pick.text or '(nothing audible)'}")
    if not rejected:
        lines.append("  (none - every spike is inside a selected clip)")
    lines += ["",
              "  A spike with nothing said is chat reacting to something on",
              "  SCREEN, which the transcript cannot see. A spike with a good",
              "  line in it means the weighting is too timid."]

    lines += ["", "5. SELECTED WITH LITTLE OR NO CHAT", "",
              "  These were carried by the words alone. If they turn out to",
              "  be the good clips, chat is not the missing piece.", ""]
    quiet = chosen_without_chat(with_chat)
    if quiet:
        for pick in quiet:
            lines.append(f"  {_clock(pick.start):>8}  chat {pick.spike:.1f}x  "
                         f"score {pick.score_chat:.1f}   {pick.text[:50]}")
    else:
        lines.append("  (none - every selection had chat behind it)")

    lines += ["", "6. VERDICT", "", "  " + verdict(with_chat, without, rates),
              "=" * 100]
    return "\n".join(lines)


def _table(heading: str, picks: list, blind: bool = False) -> list:
    lines = ["", heading, "",
             f"  {'#':>2}  {'at':>8}  {'len':>4}  {'msg/s':>6}  {'spike':>7}  "
             f"{'dB':>5}  {'laugh':>12}  {'blind':>7}  {'final':>7}  "
             f"{'moved':<24}  why"]
    if not picks:
        lines.append("  (nothing selected)")
        return lines
    for number, pick in enumerate(picks, start=1):
        lines.append(
            f"  {number:>2}  {_clock(pick.start):>8}  {pick.seconds:>3.0f}s  "
            f"{pick.per_second:>6.1f}  {pick.spike:>6.1f}x  "
            f"{pick.loudness:>5.1f}  {pick.laughter:>12}  "
            f"{pick.score_blind:>7.1f}  "
            f"{(pick.score_blind if blind else pick.score_chat):>7.1f}  "
            f"{('-' if blind else pick.movement):<24}  "
            f"{'ranked on the words alone' if blind else pick.why}")
        if pick.text:
            lines.append(f"      {pick.text[:88]}")
    return lines

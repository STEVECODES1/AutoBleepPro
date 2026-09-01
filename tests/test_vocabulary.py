"""The censor remembers what this channel says, so the next pass hears it.

The hotword list is capped - it shares the prompt region with the
verbatim instruction - and the compliance engine knows hundreds of
flagged words. The 32 that made the cut were whatever the category
dictionaries happened to yield first: an arbitrary slice, fixed forever,
with no relationship to what this streamer actually says. A channel that
says one slur four hundred times a week and has never said another was
biasing the decode toward both equally.

The censor cannot mute a word the transcript does not contain, so which
words win that budget is a safety decision.
"""

from __future__ import annotations

import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.vocabulary import (MIN_SIGHTINGS, learned,  # noqa: E402
                                 ledger_path, load, remember, save, summary)


def _path(tmp_path):
    return ledger_path(str(tmp_path))


# ── remembering ──────────────────────────────────────────────────────

def test_it_counts_instances_not_distinct_words(tmp_path):
    """A slur said four hundred times must outrank one said twice - that
    is the entire reason for keeping this."""
    path = _path(tmp_path)

    remember(path, ["nigga"] * 40 + ["shit"] * 5)

    assert load(path)["nigga"]["count"] == 40
    assert load(path)["shit"]["count"] == 5
    assert learned(path)[0] == "nigga"


def test_counts_accumulate_across_runs(tmp_path):
    """One stream is not a vocabulary."""
    path = _path(tmp_path)

    remember(path, ["nigga"] * 10)
    remember(path, ["nigga"] * 5, )

    assert load(path)["nigga"]["count"] == 15


def test_punctuation_does_not_split_a_word_in_three(tmp_path):
    """Whisper hands words back carrying their punctuation, and
    remembering "nigga", "nigga," and "nigga." separately would spend
    the budget three times on one word."""
    path = _path(tmp_path)

    remember(path, ["nigga", "nigga,", "Nigga.", "  nigga!  "])

    assert list(load(path)) == ["nigga"]
    assert load(path)["nigga"]["count"] == 4


def test_a_phrase_is_not_remembered_as_a_hotword(tmp_path):
    """Feeding a whole phrase biases the decode toward producing the
    phrase rather than toward hearing its parts."""
    path = _path(tmp_path)

    remember(path, ["kill you", "nigga"])

    assert list(load(path)) == ["nigga"]


def test_the_category_is_kept_when_it_is_known(tmp_path):
    path = _path(tmp_path)

    remember(path, ["nigga"], {"nigga": "hate_speech"})

    assert load(path)["nigga"]["category"] == "hate_speech"


# ── what gets fed back ───────────────────────────────────────────────

def test_a_one_off_is_not_learned(tmp_path):
    """One sighting is as likely to be Whisper mishearing something as a
    word this channel uses, and biasing the next decode toward a mistake
    is how a mistake becomes permanent."""
    path = _path(tmp_path)

    remember(path, ["nigga"] * MIN_SIGHTINGS + ["kike"])

    got = learned(path)
    assert "nigga" in got
    assert "kike" not in got


def test_a_word_nobody_has_said_in_months_stops_counting(tmp_path):
    """A vocabulary describes how somebody talks now."""
    path = _path(tmp_path)
    remember(path, ["ancient"] * 20)
    stale = load(path)
    stale["ancient"]["seen"] = time.time() - 200 * 86400
    save(path, stale)

    assert "ancient" not in learned(path)


def test_no_history_is_an_empty_list_not_a_crash(tmp_path):
    assert learned(str(tmp_path / "nothing-here.json")) == []
    assert load(str(tmp_path / "nothing-here.json")) == {}


def test_a_corrupt_file_is_not_fatal(tmp_path):
    path = _path(tmp_path)
    open(path, "w", encoding="utf-8").write("{not json at all")

    assert load(path) == {}
    assert learned(path) == []
    # And it can still be written over.
    assert remember(path, ["nigga", "nigga"]) == 2


# ── it actually changes what the transcriber is told ─────────────────

def test_the_learned_words_get_into_the_hotword_list(tmp_path):
    from autoreel.hotwords import build

    path = _path(tmp_path)
    remember(path, ["nigga"] * 40 + ["bitch"] * 12)

    with_history = build(work_dir=str(tmp_path), limit=12).split()

    assert "nigga" in with_history
    assert "bitch" in with_history


def test_the_most_said_words_win_the_budget(tmp_path):
    """The cap throws away the tail, so ordering IS the feature."""
    from autoreel.hotwords import build

    path = _path(tmp_path)
    remember(path, ["nigga"] * 40)

    without = build(limit=8).split()
    with_history = build(work_dir=str(tmp_path), limit=8).split()

    assert "nigga" in with_history
    assert "nigga" not in without, \
        "if the static list already had it, this test proves nothing"


def test_the_channel_name_still_comes_first(tmp_path):
    """A wrong guess at a name is visible - it lands in a burned caption
    or a title - and there are only a handful of them."""
    from autoreel.hotwords import build

    remember(_path(tmp_path), ["nigga"] * 99)

    assert build(work_dir=str(tmp_path), limit=8).split()[0] == "Stackswopo"


def test_no_history_behaves_exactly_as_it_did_before(tmp_path):
    """A fresh machine must not be worse off than before this existed."""
    from autoreel.hotwords import build

    assert build(work_dir=str(tmp_path), limit=10) == build(limit=10)


def test_an_unreadable_vocabulary_does_not_break_the_hotwords(tmp_path):
    from autoreel.hotwords import build

    open(_path(tmp_path), "w", encoding="utf-8").write("{broken")

    assert build(work_dir=str(tmp_path), limit=10) == build(limit=10)


# ── it says what it knows ────────────────────────────────────────────

def test_the_summary_says_nothing_learned_before_anything_is(tmp_path):
    assert "Nothing learned yet" in summary(_path(tmp_path))


def test_the_summary_names_the_most_said_words(tmp_path):
    path = _path(tmp_path)
    remember(path, ["nigga"] * 40 + ["shit"] * 20)

    said = summary(path)

    assert "2 distinct" in said and "60 sighting" in said
    assert "nigga" in said


# ── the censor writes it, and it holds nothing it should not ─────────

def test_the_censor_pass_records_what_it_found():
    import inspect

    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from utils import censor

    body = inspect.getsource(censor.censor_video)

    assert "from autoreel.vocabulary import ledger_path, remember" in body
    assert "work_dir=work_dir" in body, \
        "the hotword list is not being given the history"


def test_it_stores_no_chat_no_names_and_no_timestamps(tmp_path):
    """Words the account owner said into their own microphone, kept so
    their own censor can hear them better. Nothing else."""
    path = _path(tmp_path)
    remember(path, ["nigga"] * 3, {"nigga": "hate_speech"})

    entry = load(path)["nigga"]

    assert set(entry) == {"count", "category", "seen"}

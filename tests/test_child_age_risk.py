"""An age under 18 is the same YouTube child-safety risk as "minor".

10-17: "Exposing a minor's age" was caught, "he said he was 15" was not.
The numbers that are not ages - minutes, kills, "10 toes down", heights,
"she's a 10" - must stay out, or good clips are held for nothing."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from autoreel.safe_text import child_risk  # noqa: E402

RISKY = [
    "He said he was 15 on stream",
    "She’s 14 and on Monkey App",
    "15 year old caller gets roasted",
    "Roasting a 13-year-old on Omegle",
    "this girl is 16",
    "im 17 bro",
    "you're only 12",
    "Middle schooler calls in",
    "He's in 8th grade",
    "she's barely legal",
    "Exposing a minor's age",
    "kid on monkey app",
]

FINE = [
    "He's 15 minutes late to the meeting",
    "I'm 12 deep in the city",
    "She's 10 toes down for him",
    "He got 15 kills in one round",
    "She's a 10",
    "The score is 15 to 3",
    "I was 12 when I first played GTA",
    "He's 25 and still lives at home",
    "She is 18 now",
    "He's 5'9 and talking crazy",
    "He's 6 foot 4",
    "You're 9/10 on the scale",
    "2 Hours of Whiteboy Trolling Clips #1",
    "Back in high school we did this",
    "Stacks Finally Addresses The GTA RP Drama",
    "Cash app is frozen",
    # a thing's age, not a person's (a real clip line, 10-18 scan)
    "Did you know that those clips were 4 years old",
    "He's still on a 10 year old account",
    "My 5 year old dog ate it",
]


@pytest.mark.parametrize("text", RISKY)
def test_ages_under_18_are_caught(text):
    assert child_risk(text)


@pytest.mark.parametrize("text", FINE)
def test_numbers_that_are_not_ages_are_left_alone(text):
    assert not child_risk(text)

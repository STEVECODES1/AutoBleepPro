"""
Profanity compounds that better_profanity does not carry.

WHERE THESE CAME FROM
---------------------
Derived from the word list in adeel-raza/profanity-filter (MIT, Copyright
(c) 2025 Adeel Raza), which ships 1,192 entries. Our engine already
flagged 483 of them outright - the leet decoder, the affix handling and
the bypass detector cover most spelling games without needing them
enumerated - so what was actually missing was compound INSULTS:
"asshat", "fucktard", "dickweed", "shitforbrains". Those are words a
streamer says and better_profanity's base list does not know.

WHAT WAS DELIBERATELY LEFT OUT
------------------------------
That project is a VidAngel-style FAMILY CONTENT filter, not a
demonetisation filter, and roughly 400 of its entries are there to mute
adult THEMES rather than swearing: "bedroom", "affair", "betray",
"cheating", "chemistry", "caress", "bra", "climax", "adult". Muting those
would gut a stream where two people talk about relationships for an hour,
and none of them costs a video its monetisation. They are not here.

A further handful were dropped because a substring stem match pulled them
in wrongly - "passion" contains "ass", "spicy" contains "spic",
"cocktail" and "booby trap" and "unbutton" are ordinary speech. Muting an
ordinary word mid-sentence is worse than missing a rare slur: the viewer
hears a hole where a normal word was, and cannot tell why.

Multi-word entries ("butt plug") were dropped too - detection here is
per-token, so a phrase can never match, and splitting it would have added
"butt" and "plug" as standalone triggers.
"""

# Checked whole-word, lowercased, after the leet decoder has run - so
# "fuckd" here also covers "fuckd" written with a zero or a dollar sign.
EXTRA_PROFANITY: frozenset = frozenset({
    "analsex", "anilingus", "assbag", "assbagger", "assbandit", "assbite",
    "assblaster", "assclown", "asscock", "asscowboy", "asscracker",
    "assface", "assgoblin", "assh0lez", "asshat", "asshead", "assholz",
    "asshopper", "asshore", "assjacker", "assjockey", "asskiss",
    "asskisser", "assklown", "asslick", "asslicker", "asslover", "assman",
    "assmaster", "assmonkey", "assnigger", "asspacker", "asspirate",
    "asspuppies", "assrammer", "assranger", "assshit", "assshole",
    "asssucker", "asswad", "asswhore", "asswipe", "asswipes", "azz",
    "azzhole", "b00bies", "b00biez", "b00bz", "bassterd", "bassterds",
    "bastardo", "bastardz", "basterds", "basterdz", "beatoff", "bigass",
    "bigbastard", "bigbutt", "bitchass", "bitchez", "bitchslap", "bitchtit",
    "bitchy", "boobies", "bulldike", "butchdike", "butchdyke", "buttbang",
    "buttface", "buttfuck", "buttfucker", "buttfuckers", "butthead",
    "buttman", "buttmuch", "buttmunch", "buttmuncher", "buttpirate",
    "buttplug", "buttstain", "buttwipe", "c0k", "camwhore", "cazzo",
    "clitface", "clitfuck", "clitty", "cockbite", "cockblocker",
    "cockburger", "cockcowboy", "cockfucker", "cockholster", "cockjockey",
    "cockknob", "cockknocker", "cockknoker", "cocklicker", "cocklover",
    "cockmaster", "cockmongler", "cockmongruel", "cockmonkey", "cocknob",
    "cocknose", "cocknugget", "cockqueen", "cockrider", "cockshit",
    "cocksmoker", "cocksucer", "cocksuka", "cocksukka", "cocktease", "cok",
    "coksucka", "crackwhore", "cumbubble", "cumdumpster", "cumfest",
    "cumguzzler", "cumjockey", "cumlickr", "cumm", "cumqueen", "cumslut",
    "cumstain", "cumsucker", "cumtart", "cunteyed", "cuntface", "cuntfuck",
    "cuntfucker", "cunthole", "cunthunter", "cuntlick", "cuntrag",
    "cuntslut", "cuntsucker", "cuntz", "destroyyourpussy", "dickbag",
    "dickbeater", "dickbeaters", "dickbrain", "dickdipper", "dickface",
    "dickflipper", "dickforbrains", "dickfuck", "dickhole", "dickish",
    "dickjuice", "dickless", "dicklick", "dicklicker", "dickman",
    "dickmilk", "dickmonger", "dickpic", "dickripper", "dicksipper",
    "dickslap", "dickslicker", "dicksucker", "dickwad", "dickweasel",
    "dickweed", "dickwhipper", "dickwod", "dickzipper", "dipshit",
    "dixiedike", "dixiedyke", "douchewaffle", "dumshit", "easyslut",
    "fag1t", "fagbag", "faget", "fagfucker", "faggotcock", "fagit", "fagt",
    "fagtard", "fagz", "footfuck", "footfucker", "fuckable", "fuckbag",
    "fuckbitch", "fuckbook", "fuckboy", "fuckbrain", "fuckbuddy",
    "fuckbutt", "fuckd", "fuckedup", "fuckersucker", "fuckfest",
    "fuckfreak", "fuckfriend", "fuckher", "fuckina", "fuckinnuts",
    "fuckinright", "fuckit", "fuckknob", "fuckmehard", "fuckmonkey",
    "fuckn", "fucknutt", "fucknutz", "fuckpig", "fuckr", "fuckstick",
    "fuckwhore", "fuckwitt", "fuckyou", "fukah", "fuken", "fukk", "fukkah",
    "fukken", "fuktard", "fuktards", "fuxor", "gayass", "homodumbshit",
    "hotpussy", "jizzd", "jizzim", "jizzn", "jizzum", "kissass", "limpdick",
    "massterbait", "masstrbait", "masstrbate", "masturbat", "nastybitch",
    "nastyslut", "nastywhore", "penises", "pindick", "pisshead",
    "prickhead", "pussycat", "pussyeater", "pussyfucker", "pussylicker",
    "pussylips", "pussylover", "pusy", "sexwhore", "shitbox", "shitcan",
    "shitfaced", "shitfit", "shitforbrains", "shithapens", "shithappens",
    "shitlist", "shitola", "shitoutofluck", "shitstain", "skankbitch",
    "skankfuck", "skankwhore", "skanky", "skankybitch", "skankywhore",
    "slutt", "slutting", "slutty", "slutwear", "slutwhore", "smartass",
    "suckdick", "suckmyass", "suckmydick", "tranny", "vaginal",
    "whorefucker", "williewanker",
})


def contains_extra(candidate: str) -> bool:
    """True if `candidate` is one of the compounds listed above."""
    return bool(candidate) and candidate.strip().lower() in EXTRA_PROFANITY

"""
The difference between "this post failed" and "this could never post".

The circuit breaker exists to stop hammering an account that is rejecting
posts - three failures and the platform is held back until a human looks.
That is right for a rejected upload, a rate limit, an account in trouble.

It is wrong for a missing permission. A token without pages_manage_posts
will refuse every post forever, so three streams trip the breaker, and
then fixing the token leaves the platform STILL blocked until someone
knows to run --reset-failures. The user experiences that as "I fixed it
and it is still broken", which is the worst possible outcome of a safety
feature.

This is the same bug that was already fixed once for absent credentials -
an unset .env variable stopped being counted as a failure. A permission
the token was never granted is the same kind of problem wearing a
different error code: it lives in configuration, not in the account, and
no amount of waiting or retrying changes it.
"""

from __future__ import annotations

# Graph error codes that mean "the token cannot do this", not "the post
# was rejected". 200 is Meta's permissions error; 10 is a permission
# denied on the edge; 190 is an expired or invalidated token.
CONFIG_ERROR_CODES = (200, 10, 190)

# Phrases Meta uses when the answer is a scope rather than the content.
_CONFIG_PHRASES = (
    "requires", "permission", "not authorized", "access token",
    "must be an admin", "app being installed",
)


class NotConfigured(Exception):
    """The post cannot happen until something is set up.

    Raised instead of returning False so a caller cannot mistake it for a
    normal failure and record it against the circuit breaker. The message
    is what a person needs to do about it.
    """


def is_configuration_problem(code, message: str = "") -> bool:
    """True when this error is about setup rather than about the post."""
    try:
        if int(code) in CONFIG_ERROR_CODES:
            return True
    except (TypeError, ValueError):
        pass
    lowered = (message or "").lower()
    return any(phrase in lowered for phrase in _CONFIG_PHRASES)


class PermanentlyRejected(Exception):
    """The platform looked at this specific video and said no.

    Distinct from NotConfigured, which is about the account, and from a
    plain failure, which is worth trying again. Meta answers a Reel it
    cannot process with `'retriable': False` in the body - a considered
    statement that repeating the request changes nothing.

    Ignoring that field cost three identical uploads of one clip inside a
    single drain, each rejected the same way. Retrying against an
    explicit "do not retry" is not persistence, it is hammering an API
    that already answered.
    """


# Error types Meta marks `retriable: False` and then serves again fine.
#
# From publishers.log, one clip, minutes apart:
#
#   11:16:46 upload rejected (HTTP 400): {'debug_info': {'retriable':
#            False, 'type': 'ProcessingFailedError', 'message':
#            'Request processing failed'}}
#   11:17:34 ...the same
#   11:18:10 uploaded 18.6 MB for container 18423474292146676
#   11:18:18 published Reel, media_id=17959044240199078
#
# and, more plainly still, a `retriable: False` whose own message is
# "Generic Internal Error: An internal server error occurred. Please try
# again later." So the flag does not mean what it says on this error, and
# honouring it threw away clips that would have posted on the next try.
#
# The flag is still honoured everywhere else - a Reel Meta genuinely
# cannot process names what is wrong with it, and hammering that is what
# PermanentlyRejected exists to stop.
TRANSIENT_TYPES = frozenset({"processingfailederror"})


def is_permanent_rejection(payload, status: int = 0) -> bool:
    """True when Meta says retrying this upload cannot help.

    Reads `retriable` wherever it sits: Meta nests it under debug_info
    for a Reel, and at the top level elsewhere. Absent means unknown,
    which stays retriable - the ceiling and the breaker already bound
    that case, and treating silence as permanent would abandon clips
    over a dropped connection.

    `status` is the HTTP code when the caller has it. A 5xx is Meta's
    own server saying it failed, which is never a statement about this
    video.
    """
    if 500 <= int(status or 0) < 600:
        return False
    if not isinstance(payload, dict):
        return False

    holders = [h for h in (payload,
                           payload.get("error") or {},
                           (payload.get("debug_info") or {}),
                           ((payload.get("error") or {}).get("debug_info") or {}))
               if isinstance(h, dict)]

    # Checked before the flag, so a transient type wins over it.
    for holder in holders:
        if str(holder.get("type", "")).strip().lower() in TRANSIENT_TYPES:
            return False

    for holder in holders:
        if holder.get("retriable") is False:
            return True
    return False

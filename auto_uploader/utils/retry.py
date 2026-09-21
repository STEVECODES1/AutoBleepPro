"""Generic retry-with-exponential-backoff helper, shared by both uploaders."""

import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 3,
    delays: tuple = (60, 300, 900),
    on_retry: Optional[Callable[[int, int, Exception], None]] = None,
    should_retry: Optional[Callable[[Exception], bool]] = None,
) -> T:
    """Call `func()`, retrying on exception up to `max_retries` times.

    Before retry N, waits `delays[N-1]` seconds (holding the last delay if
    there are more retries than configured delays). Re-raises the final
    exception if every attempt fails. `on_retry(attempt_number, delay,
    exception)` is called right before each wait, so callers can log/notify.

    `should_retry(exception)` filters WHICH failures are worth repeating.
    Default: everything, which is the historical behaviour. Pass one when
    some failures are known to be permanent - a browser that is not
    running, a credential that is not set, a page whose form has changed.
    Waiting 60s, then 300s, then 900s to re-run a step that cannot
    possibly succeed is not resilience: it is 21 minutes of a live
    stream's publishing window spent on a foregone conclusion, and it
    buries the real cause under three identical stack traces. A failure
    the caller says is permanent is re-raised on the spot.
    """
    attempt = 0
    while True:
        try:
            return func()
        except Exception as exc:
            if attempt >= max_retries:
                raise
            if should_retry is not None and not should_retry(exc):
                raise
            delay = delays[min(attempt, len(delays) - 1)]
            if on_retry:
                on_retry(attempt + 1, delay, exc)
            time.sleep(delay)
            attempt += 1

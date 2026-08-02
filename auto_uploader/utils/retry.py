"""Generic retry-with-exponential-backoff helper, shared by both uploaders."""

import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 3,
    delays: tuple = (60, 300, 900),
    on_retry: Optional[Callable[[int, int, Exception], None]] = None,
) -> T:
    """Call `func()`, retrying on exception up to `max_retries` times.

    Before retry N, waits `delays[N-1]` seconds (holding the last delay if
    there are more retries than configured delays). Re-raises the final
    exception if every attempt fails. `on_retry(attempt_number, delay,
    exception)` is called right before each wait, so callers can log/notify.
    """
    attempt = 0
    while True:
        try:
            return func()
        except Exception as exc:
            if attempt >= max_retries:
                raise
            delay = delays[min(attempt, len(delays) - 1)]
            if on_retry:
                on_retry(attempt + 1, delay, exc)
            time.sleep(delay)
            attempt += 1

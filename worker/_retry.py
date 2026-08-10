"""Package-internal exponential-backoff retry loop shared by Worker Host calls."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def run_with_retry(
    operation: Callable[[], T],
    *,
    retriable: tuple[type[BaseException], ...],
    base_seconds: float,
    cap_seconds: float | None = None,
    terminal: tuple[type[BaseException], ...] = (),
    stop: threading.Event | None = None,
    max_attempts: int | None = None,
    on_retry: Callable[[BaseException, float], None] | None = None,
) -> T | None:
    """Run ``operation`` with exponential backoff (base, 2×base, …).

    Each wait is full-jittered — uniform in [0, backoff] — so a fleet of
    Workers recovering from a Host outage does not retry in lockstep; the
    deterministic backoff progression (and ``cap_seconds``) is unchanged.

    - ``terminal`` errors are verdicts (e.g. a 4xx): they propagate
      immediately. Terminal is checked before ``retriable`` so a subclass of
      a retriable type can still be terminal.
    - ``max_attempts`` bounds the loop; the last error is re-raised on
      exhaustion. Without it the loop runs until ``stop`` is set and then
      returns None ("stopped, try again next startup").
    - ``on_retry(error, delay)`` runs before each wait with the jittered delay.
    """
    backoff = base_seconds
    attempt = 0
    while stop is None or not stop.is_set():
        try:
            return operation()
        except terminal:
            raise
        except retriable as exc:
            attempt += 1
            if max_attempts is not None and attempt >= max_attempts:
                raise
            delay = random.uniform(0, backoff)
            if on_retry is not None:
                on_retry(exc, delay)
            if stop is None:
                time.sleep(delay)
            else:
                stop.wait(delay)
            backoff = backoff * 2 if cap_seconds is None else min(backoff * 2, cap_seconds)
    return None

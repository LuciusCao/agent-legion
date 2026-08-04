from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.services.job_intake_queue import JobIntakeQueue

logger = logging.getLogger(__name__)


async def consume_intake_batches(
    queue: JobIntakeQueue, failure_backoff_seconds: float = 1.0
) -> None:
    while True:
        try:
            processed = await asyncio.to_thread(queue.consume_once)
        except Exception:
            # A transient failure (e.g. DB hiccup while claiming) must not
            # silently kill intake consumption: log, back off, keep polling.
            logger.exception("intake queue consume failed; retrying after backoff")
            await asyncio.sleep(failure_backoff_seconds)
            continue
        await asyncio.sleep(0.05 if processed else 0.5)

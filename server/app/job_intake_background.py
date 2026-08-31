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
            # #204 broad-except audit: the intake loop's life support. This
            # coroutine is the ONLY consumer of the intake queue — if it
            # dies, new intake batches stop being claimed until process
            # restart. The outcome space of consume_once (one full claim +
            # dispatch transaction: psycopg/pool surface, dispatch-side
            # write paths) is not a business family, so a transient DB hiccup
            # while claiming must degrade to log + backoff + keep polling
            # rather than kill the loop. logger.exception keeps the
            # traceback; the backoff bounds the retry rate against a down
            # database.
            # A transient failure (e.g. DB hiccup while claiming) must not
            # silently kill intake consumption: log, back off, keep polling.
            logger.exception("intake queue consume failed; retrying after backoff")
            await asyncio.sleep(failure_backoff_seconds)
            continue
        await asyncio.sleep(0.05 if processed else 0.5)

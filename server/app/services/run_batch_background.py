"""Runs-async feeder consumption loop (#1117).

Mirrors ``server.app.job_intake_background.consume_intake_batches``: the loop
is the feeder's only life support, so a transient failure degrades to log +
backoff + keep polling rather than killing consumption until process restart.
Hosted by ``startup_tasks.BackgroundTasks`` like every other app loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.services.run_batch_feeder import RunBatchFeeder

logger = logging.getLogger(__name__)


async def consume_run_batches(feeder: RunBatchFeeder, failure_backoff_seconds: float = 1.0) -> None:
    while True:
        try:
            processed = await asyncio.to_thread(feeder.consume_once)
        except Exception:
            # #204 broad-except audit: the feeder loop's life support, the
            # intake consumer's contract verbatim. This coroutine is the ONLY
            # consumer of run_batches — if it dies, accepted async runs stop
            # being fed until process restart. consume_once's outcome space
            # (claim + chunk + cursor transactions: psycopg/pool surface,
            # resolver/materialization writes) is not a business family, so a
            # transient DB hiccup degrades to log + backoff + keep polling;
            # logger.exception keeps the traceback, the backoff bounds the
            # retry rate against a down database.
            logger.exception("run batch feeder failed; retrying after backoff")
            await asyncio.sleep(failure_backoff_seconds)
            continue
        await asyncio.sleep(0.05 if processed else 0.5)

"""Asyncio sampling loop for host operations metrics.

Kept out of startup_tasks.py to respect that module's size budget; started and
cancelled by ``BackgroundTasks`` like the other background loops.
"""

from __future__ import annotations

import asyncio
import logging

from server.app.services.ops_metrics import OpsMetricsService

logger = logging.getLogger(__name__)


async def run_ops_metrics_loop(ops_metrics: OpsMetricsService) -> None:
    interval = ops_metrics.sample_interval_seconds
    while True:
        try:
            # Catch-up instead of a single "previous minute" write: a slow
            # cycle must not leave permanent gaps in the series.
            await asyncio.to_thread(ops_metrics.sample_catch_up)
            await asyncio.to_thread(ops_metrics.cleanup_expired)
        except asyncio.CancelledError:
            raise
        except Exception:
            # #204 broad-except audit: the metrics loop's life support —
            # same discipline as the sweeper/intake loops. This task is the
            # only sampler of the ops series; dying would leave permanent
            # gaps (the catch-up comment above is exactly the anti-gap
            # mechanism, so the loop must survive to run it). The outcome
            # space is the DB write surface of sample_catch_up plus
            # cleanup_expired, not a business family; CancelledError is
            # explicitly re-raised above so shutdown still propagates.
            # logger.exception keeps the traceback and the loop retries on
            # the next interval.
            logger.exception("ops metrics sampling failed; retrying next interval")
        await asyncio.sleep(interval)

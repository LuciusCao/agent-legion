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
            await asyncio.to_thread(ops_metrics.sample_once)
            await asyncio.to_thread(ops_metrics.cleanup_expired)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ops metrics sampling failed; retrying next interval")
        await asyncio.sleep(interval)

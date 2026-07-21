from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.services.job_intake_queue import JobIntakeQueue


async def consume_intake_batches(queue: JobIntakeQueue) -> None:
    while True:
        processed = await asyncio.to_thread(queue.consume_once)
        await asyncio.sleep(0.05 if processed else 0.5)

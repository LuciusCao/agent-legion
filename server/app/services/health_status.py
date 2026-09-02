"""Health-endpoint data assembly for the execution plane (#389).

``/api/health`` (routes/common.py) keeps a fixed size budget; the pure-remote
live probe moved here, mirroring how the storage field lives in
``server/app/storage/probe.py``. In pure-remote mode (code_capacity == 0)
the online code-Worker count is probed on every call — a stalled fleet
(tasks queue silently with no Worker) must stay visible in the payload,
not only in the startup snapshot.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.agent_control.registry import AgentWorkerRegistry

logger = logging.getLogger(__name__)


def pure_remote_workers_status(app_state: Any) -> dict[str, str] | None:
    """The ``workers`` health map, with a live online-Worker count appended
    in pure-remote mode; None when no worker threads ever started."""
    workers: dict[str, str] | None = getattr(app_state, "worker_startup", None)
    if not workers or workers.get("execution_mode") != "pure_remote":
        return workers
    registry = getattr(app_state, "agent_worker_registry", None)
    if registry is None:
        return workers
    count = registry.count_online_code_workers()
    return {**workers, "online_code_workers": str(count)}


def record_pure_remote_startup(status: dict[str, str], job_db: Any) -> None:
    """Stamp the startup snapshot: execution mode + online code-Worker count.

    Called by the composition root; a zero count logs a WARNING — code nodes
    queue silently until a code Worker registers (product responsibility
    point of pure-remote mode).
    """
    status["execution_mode"] = "pure_remote"
    online = AgentWorkerRegistry(job_db).count_online_code_workers()
    status["online_code_workers"] = str(online)
    if online == 0:
        logger.warning("pure-remote: no online code Worker; code nodes queue until one registers")

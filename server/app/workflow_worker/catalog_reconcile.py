"""Low-frequency catalog reconciliation for the workflow worker poll loop.

The hot-apply pushes (workflow registration, executor publish/rollback/
archive) fire once on the write path; a transient failure there (e.g. a DB
blip mid-reload) would leave the running snapshots stale until a restart.
Re-reading both catalogs every few minutes makes them self-healing. Each
half is isolated: one failing reload never skips the other or the pass.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from server.app.executors.executor_registry_factory import reload_published_executors
from server.app.services.executor_definition_service import published_executor_definitions

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread

logger = logging.getLogger(__name__)

CATALOG_RECONCILE_INTERVAL_SECONDS = 300.0


def maybe_reconcile_catalogs(worker: WorkflowWorkerThread) -> None:
    now = time.monotonic()
    if now - worker._last_catalog_reconcile < CATALOG_RECONCILE_INTERVAL_SECONDS:
        return
    worker._last_catalog_reconcile = now
    try:
        worker.reload_scan_entries()
    except Exception:
        logger.exception("workflow scan catalog reconcile failed")
    try:
        published = published_executor_definitions(worker.settings.database_url)
        # ExecutorConfig models compare by value; rebuilding only on drift
        # keeps the steady-state pass free of adapter churn.
        if published != worker.registry.definitions():
            reload_published_executors(worker.settings, worker.registry)
    except Exception:
        logger.exception("executor catalog reconcile failed")

"""Role-split plane bridges for the composition root (#521 方案 B).

Extracted from ``main.py`` (file-size budget): the http-plane wiring —
the wakeup NOTIFY backend, the empty-claim restock relay, and the
job-event listener that folds scheduler-plane ``job_touched`` events
into the http plane's buffer — reads as one unit here instead of ten
inline lines next to the probe setup.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from server.app.configuration.host_role import ROLE_HTTP
from server.app.events.aggregator import record_job_update
from server.app.scheduler_notify import SchedulerNotifyListener
from server.app.scheduler_notify_emit import (
    notify_restock_cross_process,
    notify_schedulable_work_cross_process,
)
from server.app.scheduler_wakeup import set_notify_backend
from server.app.single_replica_probe import SingleReplicaProbe

if TYPE_CHECKING:
    from server.app.agent_broker import AgentExecutionBroker
    from server.app.events.buffer import JobEventBuffer
    from server.app.jobs import JobQueries


def combined_scheduler_plane_probe(
    role: str, start_worker: bool, job_db: JobQueries
) -> SingleReplicaProbe | None:
    """The combined role also takes the scheduler-plane lock slot.

    A combined process plus a dedicated scheduler process against one
    database (a migration-era misconfiguration) means two schedulers;
    without this second probe the pair holds disjoint keys and stays
    silent. None for the http role (that process runs no scheduler) and
    for non-worker app shapes (test/export apps).
    """
    if start_worker and role != ROLE_HTTP:
        return SingleReplicaProbe(job_db, lock_name="scheduler")
    return None


def start_role_split_runtime(
    scheduler_plane_probe: SingleReplicaProbe | None,
    job_event_listener: SchedulerNotifyListener | None,
) -> None:
    """Lifespan entry: probe the extra slot and start the listener."""
    if scheduler_plane_probe is not None:
        scheduler_plane_probe.probe()
    if job_event_listener is not None:
        job_event_listener.start()


def stop_role_split_runtime(
    scheduler_plane_probe: SingleReplicaProbe | None,
    job_event_listener: SchedulerNotifyListener | None,
) -> None:
    """Lifespan teardown: stop the listener and release the extra slot.

    Runs before the pools close — a request racing shutdown must not fire
    the listener into a closing pool, and the probe's held connection
    returns to the pool.
    """
    if job_event_listener is not None:
        job_event_listener.stop()
    if scheduler_plane_probe is not None:
        scheduler_plane_probe.close()


def install_http_plane_bridges(
    job_db: JobQueries,
    broker: AgentExecutionBroker,
    job_event_buffer: JobEventBuffer,
) -> SchedulerNotifyListener:
    """Wire the http plane's cross-process bridges; returns the listener.

    - wakeup relay: every local ``notify_schedulable_work`` also emits a
      NOTIFY (the scheduler's poll backoff is the fallback latency);
    - empty-claim restock: the debounced demand signal emits a ``restock``
      NOTIFY — the scheduler plane force-refreshes its agent-stock
      snapshot before waking (request_restock parity);
    - job-event listener: the scheduler plane's recorded job events arrive
      as ``job_touched`` NOTIFYs and fold into THIS plane's buffer, so
      dashboard SSE keeps refreshing on scheduler-driven transitions.

    The caller owns the listener's lifecycle (start in the lifespan,
    stop before the pools close).
    """
    set_notify_backend(partial(notify_schedulable_work_cross_process, job_db))
    broker.empty_claim.on_empty_queue = partial(notify_restock_cross_process, job_db)
    return SchedulerNotifyListener(
        job_db,
        on_job_touched=partial(record_job_update, job_db, job_event_buffer),
    )


def relay_job_event_emitter(job_db: JobQueries) -> Callable[[str], None]:
    """The scheduler plane's job-event relay (cross_plane_event wiring)."""
    from server.app.scheduler_notify_emit import notify_job_touched_cross_process

    return partial(notify_job_touched_cross_process, job_db)

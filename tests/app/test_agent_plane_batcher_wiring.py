"""#591 agent-plane batcher wiring tests (no app, no threads started).

The plane-level contract only: the knob decides whether the batcher is
constructed, both repositories share the ONE instance, and the arms point
at the repository methods (the thread lifecycle is the app lifespan's job
— tests never start it here). The ``job_db`` fixture rides the postgres
tier (the repository constructor runs init_db).
"""

from __future__ import annotations

import pytest

from server.app.bootstrap.agent_plane import build_agent_plane
from server.app.events import JobEventManager
from server.app.events.agents import AgentStatusManager
from server.app.events.buffer import JobEventBuffer
from server.app.events.bus import InProcessEventBus
from server.app.worker_control import WorkspaceWorkerControl


def _plane(job_db, settings, enabled: bool, app_result_batching: bool = True):
    settings.executor_runtime.agent_workers.result_commit_batching = enabled
    return build_agent_plane(
        job_db,
        settings,
        AgentStatusManager(),
        WorkspaceWorkerControl(job_db),
        artifact_store=None,
        job_event_manager=JobEventManager(InProcessEventBus()),
        job_event_buffer=JobEventBuffer(job_db),
        result_batching=app_result_batching,
    )


def test_knob_off_leaves_batcher_unconstructed(job_db, settings) -> None:
    plane = _plane(job_db, settings, enabled=False)
    assert plane.result_commit_batcher is None
    assert plane.executor_leases.result_batcher is None
    assert plane.broker.result_batcher is None


def test_start_worker_false_plane_carries_no_batcher(job_db, settings) -> None:
    """C1: a plane built for a start_worker=False app (tests/export) must
    not carry a batcher — its lifespan never starts the writer, and every
    submit would park on an undrained future."""
    plane = _plane(job_db, settings, enabled=True, app_result_batching=False)
    assert plane.result_commit_batcher is None
    assert plane.executor_leases.result_batcher is None
    assert plane.broker.result_batcher is None


@pytest.mark.postgres
def test_knob_on_shares_one_batcher(job_db, settings) -> None:
    plane = _plane(job_db, settings, enabled=True)
    batcher = plane.result_commit_batcher
    assert batcher is not None
    assert plane.executor_leases.result_batcher is batcher
    assert plane.broker.result_batcher is batcher
    assert batcher.mark_done_many == plane.broker.mark_done_many
    # The finish arm is the retry-wrapped batch module bound to the repo
    # (partial — repo-method identity does not hold by design).
    assert callable(batcher.finish_many)
    # The writer thread is NOT started by the plane (lifespan owns it).
    assert batcher._thread is None

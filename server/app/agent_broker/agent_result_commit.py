"""Blocking commit path for the Agent Worker result endpoint.

Split out of ``routes/agent_workers.py`` so the route can offload the
commit to the threadpool (holding the loop would stall heartbeat, claim,
and dashboard streams at agent scale)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from server.app.agent_broker import AgentExecutionBroker, worker_events
from server.app.agent_broker.result_spool import publish_staged_result
from server.app.agent_broker.result_timing import ResultStageTimer, report_result_stages
from server.app.agent_control.completion import (
    AgentCompletionHandler,
    AgentOutcome,
    report_auth_failure_safe,
)


def commit_agent_result(
    broker: AgentExecutionBroker,
    completion: AgentCompletionHandler,
    execution_id: str,
    worker_id: str,
    lease_id: str,
    outcome: AgentOutcome,
    record: dict[str, Any],
    staged_body: Path,
) -> None:
    """Persist the archive and commit the terminal state; raises HTTPException.

    ``staged_body`` is atomically renamed into place here; the route reclaims
    it when the commit fails."""
    payload = broker.claimed_payload(execution_id, worker_id)
    if payload is None or str(payload["lease_id"]) != lease_id:
        worker_events.note_execution_finished_rejected(execution_id, worker_id)
        raise HTTPException(status_code=409, detail="execution is not owned by this Worker")
    if broker.bundle_dir is None:
        raise HTTPException(status_code=500, detail="Agent bundle storage is unavailable")
    archive_name = f"{execution_id}.{uuid.uuid4().hex}.result.tar.gz"
    succeeded = False
    # #521 result-stage split: one timer spans this commit's unpack → … →
    # mark_done sequence; the route's result_timer (#359) keeps the
    # spool-inclusive total. Attempt-level best-effort, reports on every
    # exit path (409s included), mirroring the claim timer (#448).
    stage_timer = ResultStageTimer()
    try:
        publish_staged_result(staged_body, broker.bundle_dir / archive_name)
        # finish() commits the lease/node terminal state first; mark_done()
        # then closes the request (bound to lease_id in SQL). A crash
        # between the two leaves a claimed request whose lease is no
        # longer active, which the sweeper closes instead of requeueing.
        finished = completion.finish(  # fmt: skip
            lease_id=lease_id,
            worker_id=worker_id,
            job_id=str(payload["job_id"]),
            node_key=str(payload["node_key"]),
            manifest=payload["manifest"],
            outcome=outcome,
            archive_name=archive_name,
            stage_timer=stage_timer,
        )
        if not finished:
            raise HTTPException(status_code=409, detail="execution lease is no longer active")
        if broker.mark_done(execution_id, worker_id, lease_id, record) is None:
            worker_events.note_execution_finished_rejected(execution_id, worker_id, payload)
            raise HTTPException(status_code=409, detail="execution is no longer owned")
        stage_timer.stage("mark_done")
        succeeded = True
        # #490 execution.finished: outcome + wall time (claim → committed
        # result spans download/run/upload); claimed_at is read post-done,
        # so the wall time is best-effort.
        worker_events.note_execution_finished(
            execution_id, worker_id, payload, outcome, broker.database_dsn
        )
        # Runtime profile (#359): execute-stage done rate (a worker execution
        # reached its terminal state through the normal result path).
        from server.app.services.runtime_profile import profile

        profile.note_execution_done()
        if outcome.auth_failure_connection:
            # Batch 2 (design §5.3): the node recorded an upstream auth
            # failure; the Host performs the privileged invalidation.
            report_auth_failure_safe(broker.database_dsn, outcome.auth_failure_connection)
    finally:
        report_result_stages(
            stage_timer, execution_id=execution_id, worker_id=worker_id, committed=succeeded
        )
        # The archive name is unique to this attempt — always reclaim it.
        broker.discard_result_archive(archive_name)
        if succeeded:
            # Only a fully committed result retires the shared execution
            # bundle. On 409/500 paths the bundle must survive for
            # re-queued attempts; terminal-request bundles are reaped by
            # the sweeper (AgentExecutionBroker.reap_terminal_bundles).
            broker.retire_bundle(str(payload["manifest"].get("bundle_name", "")))

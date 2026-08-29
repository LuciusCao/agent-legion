"""Blocking commit path for the Agent Worker result endpoint.

Split out of ``routes/agent_workers.py`` so the route handler can offload the
DB/disk commit to the threadpool (at agent scale, multiple reports per second
each committing ``finish()`` + ``mark_done()`` write transactions would hold
the event loop and stall every heartbeat, claim, and dashboard stream).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.result_spool import publish_staged_result
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

    ``staged_body`` is the staging file the route streamed the request body
    into; it is atomically renamed into place here, and the route reclaims
    it if this commit never renames it."""
    payload = broker.claimed_payload(execution_id, worker_id)
    if payload is None or str(payload["lease_id"]) != lease_id:
        raise HTTPException(status_code=409, detail="execution is not owned by this Worker")
    if broker.bundle_dir is None:
        raise HTTPException(status_code=500, detail="Agent bundle storage is unavailable")
    archive_name = f"{execution_id}.{uuid.uuid4().hex}.result.tar.gz"
    archive_path = broker.bundle_dir / archive_name
    succeeded = False
    try:
        publish_staged_result(staged_body, archive_path)
        # finish() commits the lease/node terminal state first; mark_done()
        # then closes the request (bound to lease_id in SQL). A crash
        # between the two leaves a claimed request whose lease is no
        # longer active, which the sweeper closes instead of requeueing.
        finished = completion.finish(
            lease_id=lease_id,
            worker_id=worker_id,
            job_id=str(payload["job_id"]),
            node_key=str(payload["node_key"]),
            manifest=payload["manifest"],
            outcome=outcome,
            archive_name=archive_name,
        )
        if not finished:
            raise HTTPException(status_code=409, detail="execution lease is no longer active")
        if broker.mark_done(execution_id, worker_id, lease_id, record) is None:
            raise HTTPException(status_code=409, detail="execution is no longer owned")
        succeeded = True
        if outcome.auth_failure_connection:
            # Batch 2 (design §5.3): the node recorded an upstream auth
            # failure; the Host performs the privileged invalidation.
            report_auth_failure_safe(broker.database_dsn, outcome.auth_failure_connection)
    finally:
        # The archive name is unique to this attempt — always reclaim it.
        broker.discard_result_archive(archive_name)
        if succeeded:
            # Only a fully committed result retires the shared execution
            # bundle. On 409/500 paths the bundle must survive for
            # re-queued attempts; terminal-request bundles are reaped by
            # the sweeper (AgentExecutionBroker.reap_terminal_bundles).
            broker.retire_bundle(str(payload["manifest"].get("bundle_name", "")))

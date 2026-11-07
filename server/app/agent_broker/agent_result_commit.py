"""Blocking commit path for the Agent Worker result endpoint.

Split out of ``routes/agent_workers.py`` so the route can offload the
commit to the threadpool (holding the loop would stall heartbeat, claim,
and dashboard streams at agent scale)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.agent_result_commit_effects import (
    cleanup_result_commit,
    note_committed_result,
)
from server.app.agent_broker.lease_reclaim_audit import reject_result
from server.app.agent_broker.result_output_manifest import (
    enrich_outcome_from_archived_manifest,
)
from server.app.agent_broker.result_spool import publish_staged_result
from server.app.agent_broker.result_timing import ResultStageTimer
from server.app.agent_control.completion import AgentCompletionHandler, AgentOutcome


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
    # #681: every 409 below is audited (reason / artifacts) by reject_result.
    audit = (broker.database_dsn, execution_id, worker_id, lease_id, record)
    payload = broker.claimed_payload(execution_id, worker_id)
    if payload is None or str(payload["lease_id"]) != lease_id:
        size = staged_body.stat().st_size if staged_body.exists() else None
        detail = "execution is not owned by this Worker"
        raise reject_result(*audit, stage="commit", detail=detail, archive_bytes=size)
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
        # #755 codex P1（v1 legacy 换轨形态，旧 Worker）：头溢出时把头里
        # 放不下的直传 ref 清单写成归档首成员，头里只带
        # output_artifacts_in_archive 标记。这里在 finish 之前读回清单并
        # enrich outcome：空清单翻转、HEAD 校验、staged promote 全部走
        # 既有路径，finish 侧零改动。#843 PR-2 起新 Worker 不再发该标记
        # （v2 读侧显式剥离）；本臂只服务旧 Worker 兼容窗。版本偏斜说明：
        # 带 v1 换轨标记的新形态 + 不认识该标记的旧 Host 会走空清单翻转
        # 诚实判败（completed + 空清单 → failed），不会静默错。
        if record.get("output_artifacts_in_archive") is True:
            outcome = enrich_outcome_from_archived_manifest(
                outcome, record, broker.bundle_dir / archive_name
            )
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
            detail = "execution lease is no longer active"
            raise reject_result(*audit, stage="finish", detail=detail, payload=payload)
        if broker.mark_done(execution_id, worker_id, lease_id, record) is None:
            detail = "execution is no longer owned"
            raise reject_result(*audit, stage="mark_done", detail=detail, payload=payload)
        stage_timer.stage("mark_done")
        succeeded = True
        note_committed_result(broker, execution_id, worker_id, payload, outcome)
    finally:
        cleanup_result_commit(
            broker,
            stage_timer,
            execution_id=execution_id,
            worker_id=worker_id,
            committed=succeeded,
            archive_name=archive_name,
            payload=payload,
        )

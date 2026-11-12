"""#724 acceptance: the sync and async intake paths run the same
JobCreationPipeline sequence, so identical input must produce an equivalent
job set either way.

The two paths' outputs are compared modulo the workspace-scoped prefixes
(job id / run id): same input values in two freshly-seeded workspaces must
yield the same (source_type, source_id, title, stem, input, frozen config,
node keys) job set. The cross-family case (items vs intake values) is not
meaningful — items and intake values resolve through different candidate
families — so the run-service half of the extraction is covered by the
existing test_run_service* regression suite, and this file pins the
sync/async pair that accepts the SAME input.
"""

from __future__ import annotations

import json
from typing import Any

from server.app.services.demo_node_seed import seed_demo_workspace_node_codes
from server.app.services.job_intake import JobIntakeService
from server.app.services.job_intake_queue import JobIntakeQueue
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_demo_legacy_intake_definition


def _workspace(job_db, settings, workspace_id: str) -> None:
    job_db.create_workspace(workspace_id)
    seed_demo_workspace_node_codes(settings, workspace_id)
    # The demo workflow retired intake modes (#154); the job-batches intake
    # path needs the legacy variant published as the active revision.
    WorkflowRevisionService(job_db).ensure_active_revision(
        workspace_id, load_demo_legacy_intake_definition()
    )


def _submit(job_db, settings, workspace_id: str, values: list[str], *, async_processing: bool):
    return JobIntakeService(job_db, settings).create_batch(
        workspace_id,
        {
            # Workspace id IS the workflow key (#211); using the production
            # shape keeps the deterministic job ids comparable across paths.
            "workflow_key": workspace_id,
            "source_kind": "direct_ids",
            "entity": "question",
            "knowledge_point_ids": values,
            "async_processing": async_processing,
        },
    )


def _drain_queue(job_db, settings) -> None:
    queue = JobIntakeQueue(job_db, settings)
    while queue.consume_once():
        pass


def _normalized_json(value: Any) -> str:
    if value is None:
        return ""
    parsed = json.loads(value) if isinstance(value, str) else value
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def _job_snapshot(job_db, workspace_id: str) -> list[tuple[Any, ...]]:
    """The workspace's job set as path-independent tuples (sorted)."""
    with job_db.connect() as conn:
        rows = conn.execute(
            "select source_type, source_id, title, stem, input_json, frozen_config_json"
            " from jobs where workspace_id=%s order by source_id",
            (workspace_id,),
        ).fetchall()
        node_rows = conn.execute(
            "select j.source_id, n.node_key from job_nodes n"
            " join jobs j on j.id = n.job_id where j.workspace_id=%s"
            " order by j.source_id, n.node_key",
            (workspace_id,),
        ).fetchall()
    nodes_by_source: dict[str, list[str]] = {}
    for row in node_rows:
        nodes_by_source.setdefault(str(row["source_id"]), []).append(str(row["node_key"]))
    return [
        (
            str(row["source_type"]),
            str(row["source_id"]),
            str(row["title"]),
            str(row["stem"]),
            _normalized_json(row["input_json"]),
            _normalized_json(row["frozen_config_json"]),
            nodes_by_source.get(str(row["source_id"]), []),
        )
        for row in rows
    ]


def test_sync_and_async_intake_produce_equivalent_jobs(job_db, settings) -> None:
    _workspace(job_db, settings, "ws-eq-sync")
    _workspace(job_db, settings, "ws-eq-async")

    # A pre-existing job in both workspaces: the main submission must dedup
    # it identically on both paths.
    _submit(job_db, settings, "ws-eq-sync", ["Q00"], async_processing=False)
    _submit(job_db, settings, "ws-eq-async", ["Q00"], async_processing=True)
    _drain_queue(job_db, settings)

    # 29 fresh values > INTAKE_QUEUE_CHUNK_SIZE (25) so the async path spans
    # two queue chunks; the Q05 repeat exercises intra-request dedup; Q00 is
    # the pre-existing duplicate.
    values = ["Q00"] + [f"Q{i:02d}" for i in range(1, 30)] + ["Q05"]
    sync_result = _submit(job_db, settings, "ws-eq-sync", values, async_processing=False)
    async_result = _submit(job_db, settings, "ws-eq-async", values, async_processing=True)
    assert sync_result["created_count"] == 29
    # Async acceptance creates nothing in the request thread.
    assert async_result["created_count"] == 0
    _drain_queue(job_db, settings)

    async_run = job_db.get_run(str(async_result["batch"]["id"]))
    assert str(async_run["status"]) == "completed"
    assert int(async_run["created_count"]) == 29

    assert _job_snapshot(job_db, "ws-eq-sync") == _job_snapshot(job_db, "ws-eq-async")

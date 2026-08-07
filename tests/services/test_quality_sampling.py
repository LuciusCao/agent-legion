"""Quality sampling service: deterministic batches and snapshot fields (schema v28)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from server.app.db.transaction import read_connection, write_transaction
from server.app.services.job_errors import NotFoundError
from server.app.services.quality_sampling import QualitySamplingService
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.fresh_schema

WORKSPACE = "ws-quality"


def _service() -> QualitySamplingService:
    return QualitySamplingService(TEST_DATABASE_URL)


def _seed_workspace(conn, workspace_id: str = WORKSPACE) -> None:
    conn.execute(
        "insert into workspaces(id, name) values (%s, %s) on conflict do nothing",
        (workspace_id, workspace_id),
    )


def _seed_run(
    conn,
    *,
    run_id: int,
    job_id: str,
    workspace_id: str = WORKSPACE,
    workflow_key: str = "wf-a",
    node_key: str = "node-a",
    status: str = "completed",
    skill_version: str = "v1",
    started_at: datetime | None = None,
    failure_category: str = "",
    failure_detail: str = "",
) -> None:
    conn.execute(
        "insert into jobs(id, workspace_id, workflow_key, source_type, source_id) "
        "values (%s, %s, %s, %s, %s) on conflict (id) do nothing",
        (job_id, workspace_id, workflow_key, "test", job_id),
    )
    conn.execute(
        "insert into node_runs("
        "id, job_id, node_key, status, skill_version, failure_category, failure_detail,"
        " started_at)"
        " values (%s, %s, %s, %s, %s, %s, %s, coalesce(%s, current_timestamp))",
        (
            run_id,
            job_id,
            node_key,
            status,
            skill_version,
            failure_category,
            failure_detail,
            started_at,
        ),
    )


def _seed_runs(count: int, **kwargs) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        for index in range(count):
            _seed_run(conn, run_id=index + 1, job_id=f"job-{index + 1}", **kwargs)


def _item_run_ids(batch_id: str) -> list[int]:
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select node_run_id from quality_sample_items where batch_id = %s order by node_run_id",
            (batch_id,),
        ).fetchall()
    return [int(row["node_run_id"]) for row in rows]


def _single_item(batch_id: str) -> dict:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select * from quality_sample_items where batch_id = %s", (batch_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def test_sampling_is_deterministic_for_same_seed():
    _seed_runs(10)
    service = _service()
    first = service.create_batch(WORKSPACE, name="b1", sample_size=5, seed="seed-1")
    second = service.create_batch(WORKSPACE, name="b2", sample_size=5, seed="seed-1")
    assert first["sampled_count"] == 5
    assert second["sampled_count"] == 5
    assert _item_run_ids(first["id"]) == _item_run_ids(second["id"])


def test_sample_size_limits_items():
    _seed_runs(10)
    batch = _service().create_batch(WORKSPACE, name="b", sample_size=4, seed="s")
    assert batch["sampled_count"] == 4
    assert len(_item_run_ids(batch["id"])) == 4


def test_filters_narrow_candidates():
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        for index in range(5):
            _seed_run(conn, run_id=index + 1, job_id=f"job-a-{index}", node_key="node-a")
        for index in range(3):
            _seed_run(
                conn,
                run_id=100 + index,
                job_id=f"job-b-{index}",
                node_key="node-b",
                status="failed",
                workflow_key="wf-b",
            )
    service = _service()
    batch = service.create_batch(
        WORKSPACE,
        name="b",
        sample_size=50,
        seed="s",
        workflow_key="wf-b",
        node_keys=["node-b"],
        statuses=["failed"],
    )
    assert batch["sampled_count"] == 3
    assert _item_run_ids(batch["id"]) == [100, 101, 102]


def test_time_window_filter():
    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 2, 1, tzinfo=UTC)
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        for index in range(2):
            _seed_run(conn, run_id=index + 1, job_id=f"job-early-{index}", started_at=early)
        for index in range(3):
            _seed_run(conn, run_id=10 + index, job_id=f"job-late-{index}", started_at=late)
    batch = _service().create_batch(
        WORKSPACE,
        name="b",
        sample_size=50,
        seed="s",
        since=datetime(2026, 1, 15, tzinfo=UTC),
    )
    assert batch["sampled_count"] == 3
    assert _item_run_ids(batch["id"]) == [10, 11, 12]


def test_snapshot_fields_come_from_usage_request_and_entities():
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        _seed_run(
            conn,
            run_id=1,
            job_id="job-1",
            skill_version="skill-v2",
            status="failed",
            failure_category="timeout",
            failure_detail="worker timeout",
        )
        conn.execute(
            "insert into node_run_token_usage("
            "node_run_id, job_id, workspace_id, node_key, provider, model)"
            " values (1, 'job-1', %s, 'node-a', 'gateway', 'model-x')",
            (WORKSPACE,),
        )
        conn.execute(
            "insert into agent_execution_requests("
            "execution_id, workspace_id, job_id, workflow_key, node_key, agent_id,"
            " agent_definition_hash, node_concurrency_limit, state, node_run_id,"
            " queued_at, manifest_json)"
            " values ('exec-1', %s, 'job-1', 'wf-a', 'node-a', 'agent-1', 'hash-1', 1,"
            " 'done', 1, current_timestamp, %s)",
            (WORKSPACE, json.dumps({"capability": "review_keywords"})),
        )
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('ve-1', 'agent', %s, 'agent-1', 7, 'published', '{}', 'hash-1', 'test')",
            (WORKSPACE,),
        )
    batch = _service().create_batch(WORKSPACE, name="b", sample_size=5, seed="s")
    item = _single_item(batch["id"])
    assert item["node_run_id"] == 1
    assert item["job_id"] == "job-1"
    assert item["node_key"] == "node-a"
    assert item["run_status"] == "failed"
    assert item["skill_version"] == "skill-v2"
    assert item["failure_category"] == "timeout"
    assert item["failure_detail"] == "worker timeout"
    assert item["provider"] == "gateway"
    assert item["model"] == "model-x"
    assert item["capability"] == "review_keywords"
    assert item["agent_definition_hash"] == "hash-1"
    assert item["agent_version"] == 7


def test_seed_is_generated_when_missing():
    _seed_runs(1)
    batch = _service().create_batch(WORKSPACE, name="b", sample_size=5)
    assert batch["seed"]


def test_missing_workspace_raises_not_found():
    with pytest.raises(NotFoundError):
        _service().create_batch("ws-missing", name="b", sample_size=5)

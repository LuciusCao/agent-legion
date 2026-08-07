"""批量执行路径（_job_rerun_batch）与逐条 rerun() 的等价性 + 读查询上界。"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import server.app.executors.leases as leases_module
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_catalog import WorkflowCatalogService

_NODE_KEYS = ["fetch_questions", "clean_and_parse", "generate_key_info"]


@pytest.fixture
def rerun_service(job_db, settings):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db.path, data_dir=settings.data_dir),
        settings,
        WorkflowCatalogService(settings),
        JobArtifactMutationService(settings.jobs_dir),
    )


def _create_job(job_db, workspace_id: str, source_id: str) -> dict[str, Any]:
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace_id,
    )
    return job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id=source_id,
        batch_id=batch["id"],
        title=source_id,
        node_keys=_NODE_KEYS,
        workspace_id=workspace_id,
    )


def _add_active_lease(job_db, job: dict[str, Any], node_key: str, lease_id: str) -> None:
    run = job_db.start_node_run(
        job["id"], node_key, ["cmd"], f"logs/jobs/{job['id']}-{node_key}.log"
    )
    assert run is not None
    now = datetime.now(UTC)
    with job_db.connect() as conn:
        conn.execute(
            "insert into executor_leases("
            "id, execution_id, executor_id, workspace_id, job_id, workflow_key,"
            " node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at)"
            " values (%s, %s, 'code-default', %s, %s, %s, %s, %s, 'active', %s, %s, %s)",
            (
                lease_id,
                f"exec-{lease_id}",
                job["workspace_id"],
                job["id"],
                job["workflow_key"],
                node_key,
                run["id"],
                database_timestamp(now),
                database_timestamp(now),
                database_timestamp(now + timedelta(seconds=300)),
            ),
        )


def _seed_mixed(job_db, tag: str) -> dict[str, Any]:
    """Mixed-scenario dataset; *tag* keeps job ids unique across reseeds.

    The workspace name stays constant so message strings (which embed it)
    compare equal across the per-job and batch datasets.
    """
    ws = job_db.create_workspace("batch-eq", default_workflow_key="question_comprehension_info")
    ws_id = str(ws["id"])
    ok_a = _create_job(job_db, ws_id, f"{tag}-ok-a")
    ok_b = _create_job(job_db, ws_id, f"{tag}-ok-b")
    running = _create_job(job_db, ws_id, f"{tag}-running")
    leased = _create_job(job_db, ws_id, f"{tag}-leased")
    failed_node = _create_job(job_db, ws_id, f"{tag}-failed-node")
    not_failed = _create_job(job_db, ws_id, f"{tag}-not-failed")
    other = job_db.create_workspace(
        f"batch-{tag}-other", default_workflow_key="question_comprehension_info"
    )
    foreign = _create_job(job_db, str(other["id"]), f"{tag}-foreign")

    for job in (ok_a, ok_b, running, leased, failed_node):
        job_db.update_job_status(job["id"], "failed", "boom")
    job_db.update_job_node(running["id"], "clean_and_parse", status="running")
    _add_active_lease(job_db, leased, "clean_and_parse", f"lease-{tag}")
    job_db.update_job_node(failed_node["id"], "clean_and_parse", status="failed")

    return {
        "ws_id": ws_id,
        "ws_name": str(ws["name"]),
        "node_mode_ids": [
            str(ok_a["id"]),
            str(ok_b["id"]),
            str(running["id"]),
            str(leased["id"]),
            str(foreign["id"]),
            f"{tag}-missing",
        ],
        "failed_mode_ids": [str(failed_node["id"]), str(not_failed["id"]), f"{tag}-missing"],
        "failed_node_job": str(failed_node["id"]),
    }


def _per_job_results(
    service: JobRerunService,
    workspace_id: str,
    ids: list[str],
    node_key: str | None,
    from_failed_node: bool = False,
) -> list[dict[str, Any]]:
    """The pre-bulk batch loop: per-job rerun() with to_result capture."""
    results = []
    for job_id in ids:
        try:
            results.append(
                service.rerun(workspace_id, job_id, node_key, from_failed_node=from_failed_node)
            )
        except JobOperationError as exc:
            results.append(exc.to_result())
    return results


def _strip_job_ids(results: list[dict[str, Any]], ws_name: str) -> list[dict[str, Any]]:
    """Drop job ids and normalize workspace-name-bearing messages.

    create_workspace dedupes duplicate names with a numeric suffix, and the
    returned row carries the requested name — strip any suffix via regex.
    """
    stripped = []
    for result in results:
        row = {key: value for key, value in result.items() if key != "job_id"}
        if row["message"]:
            row["message"] = re.sub(rf"{re.escape(ws_name)}(_\d+)?", "<ws>", str(row["message"]))
        stripped.append(row)
    return stripped


def test_batch_rerun_matches_per_job_rerun_node_mode(rerun_service, job_db) -> None:
    dataset_a = _seed_mixed(job_db, "A")
    expected = _per_job_results(
        rerun_service, dataset_a["ws_id"], dataset_a["node_mode_ids"], "clean_and_parse"
    )

    dataset_b = _seed_mixed(job_db, "B")
    actual = rerun_service.batch_rerun(
        dataset_b["ws_id"], dataset_b["node_mode_ids"], "clean_and_parse"
    )

    assert _strip_job_ids(actual, dataset_b["ws_name"]) == _strip_job_ids(
        expected, dataset_a["ws_name"]
    )
    statuses = [r["status"] for r in actual]
    assert statuses == ["succeeded", "succeeded", "skipped", "skipped", "failed", "failed"]
    assert actual[2]["reason_code"] == "busy"  # running nodes
    assert actual[3]["reason_code"] == "busy"  # active lease
    assert actual[4]["reason_code"] == "wrong_workspace"
    assert actual[5]["reason_code"] == "not_found"


def test_batch_rerun_matches_per_job_rerun_from_failed_node(rerun_service, job_db) -> None:
    dataset_a = _seed_mixed(job_db, "C")
    expected = _per_job_results(
        rerun_service,
        dataset_a["ws_id"],
        dataset_a["failed_mode_ids"],
        None,
        from_failed_node=True,
    )

    dataset_b = _seed_mixed(job_db, "D")
    actual = rerun_service.batch_rerun(
        dataset_b["ws_id"], dataset_b["failed_mode_ids"], from_failed_node=True
    )

    assert _strip_job_ids(actual, dataset_b["ws_name"]) == _strip_job_ids(
        expected, dataset_a["ws_name"]
    )
    assert [r["status"] for r in actual] == ["succeeded", "skipped", "failed"]
    assert actual[0]["node_key"] == "clean_and_parse"
    assert actual[1]["reason_code"] == "not_failed"


def test_category_batch_matches_per_job_rerun_targets(rerun_service, job_db) -> None:
    """technical=rerun_self / business=rerun_upstream：类别批量结果与逐条
    rerun 同一目标节点的结果一致。"""
    ws = job_db.create_workspace("cat-eq", default_workflow_key="question_comprehension_info")
    ws_id = str(ws["id"])
    technical_job = _create_job(job_db, ws_id, "cat-tech")
    business_job = _create_job(job_db, ws_id, "cat-biz")
    missing_job = _create_job(job_db, ws_id, "cat-missing")
    _fail_run(job_db, technical_job, "clean_and_parse", "technical", "model_error")
    _fail_run(job_db, business_job, "clean_and_parse", "business", "empty_content")

    results = rerun_service.rerun_by_failure_category(
        ws_id, "technical", job_ids=[technical_job["id"], missing_job["id"]]
    )
    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["clean_and_parse"]
    assert results[1]["status"] == "skipped"
    assert results[1]["reason_code"] == "no_matching_failure"

    results = rerun_service.rerun_by_failure_category(
        ws_id, "business", job_ids=[business_job["id"]]
    )
    assert results[0]["status"] == "succeeded"
    # rerun_upstream：clean_and_parse 的上游是 fetch_questions。
    assert results[0]["rerun_nodes"] == ["fetch_questions"]
    nodes = {n["node_key"]: n["status"] for n in job_db.list_job_nodes(business_job["id"])}
    assert nodes["fetch_questions"] == "pending"


def _fail_run(job_db, job: dict[str, Any], node_key: str, category: str, detail: str) -> None:
    run = job_db.start_node_run(
        job["id"], node_key, ["cmd"], f"logs/jobs/{job['id']}-{node_key}.log"
    )
    assert run is not None
    with job_db.connect() as conn:
        conn.execute(
            "update node_runs set status='failed', error_message='boom',"
            " failure_category=%s, failure_detail=%s, finished_at=current_timestamp"
            " where id=%s",
            (category, detail, run["id"]),
        )
        conn.execute(
            "update job_nodes set status='failed', error_message='boom'"
            " where job_id=%s and node_key=%s",
            (job["id"], node_key),
        )
        conn.execute("update jobs set status='failed' where id=%s", (job["id"],))
        conn.execute("commit")


def _count_read_connections(job_db, monkeypatch) -> dict[str, int]:
    counter = {"n": 0}
    original_connect = type(job_db)._connect_read

    @contextmanager
    def counting_connect(self):
        counter["n"] += 1
        with original_connect(self) as conn:
            yield conn

    monkeypatch.setattr(type(job_db), "_connect_read", counting_connect)
    original_read = leases_module.read_connection

    @contextmanager
    def counting_read(dsn):
        counter["n"] += 1
        with original_read(dsn) as conn:
            yield conn

    monkeypatch.setattr(leases_module, "read_connection", counting_read)
    return counter


def test_batch_rerun_read_queries_bounded(rerun_service, job_db, monkeypatch) -> None:
    """300 个可重跑 job 的批量执行：读查询常数级（写事务不在此计量）。"""
    ws = job_db.create_workspace("batch-perf", default_workflow_key="question_comprehension_info")
    ws_id = str(ws["id"])
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": [f"P{i}" for i in range(300)]},
        workspace_id=ws_id,
    )
    ids = []
    for i in range(300):
        job = job_db.create_job(
            workflow_key="question_comprehension_info",
            source_type="question",
            source_id=f"P{i}",
            batch_id=batch["id"],
            title=f"P{i}",
            node_keys=_NODE_KEYS,
            workspace_id=ws_id,
        )
        job_db.update_job_status(job["id"], "failed", "boom")
        ids.append(str(job["id"]))

    counter = _count_read_connections(job_db, monkeypatch)
    results = rerun_service.batch_rerun(ws_id, ids, "fetch_questions")

    assert [r["status"] for r in results] == ["succeeded"] * 300
    # bulk 窄表 jobs + nodes + leases + 全量行（写用）各一次。
    assert counter["n"] <= 8

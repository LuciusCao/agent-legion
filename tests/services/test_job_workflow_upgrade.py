import json
from contextlib import closing
from pathlib import Path

import pytest

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_selection_resolver import EmptyJobSelectionError
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.job_workflow_upgrade_batch import batch_upgrade
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers import load_builtin_definition
from tests.postgres_support import TEST_DATABASE_URL


class _RecordingEventBuffer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def record_job_updated(self, workspace_id: str, job_id: str) -> None:
        self.calls.append((workspace_id, job_id))


class _RecordingEventManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def broadcast_job_updated(self, workspace_id: str, job_id: str, stats: dict) -> None:
        self.calls.append((workspace_id, job_id, stats))


def test_upgrade_job_workflow_updates_revision_and_rebuilds_nodes(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    current = revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    queries.update_job_node(job["id"], "fetch_items", status="completed")
    queries.update_job_status(job["id"], "completed")
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )

    result = service.upgrade(workspace["id"], job["id"])

    upgraded = queries.get_job(job["id"])
    assert result["status"] == "succeeded"
    assert result["operation"] == "upgrade_workflow"
    assert upgraded["workflow_revision_id"] == current["id"]
    assert upgraded["workflow_version"] == current["version"]
    assert upgraded["workflow_definition_hash"] == current["definition_hash"]
    assert upgraded["status"] == "queued"
    assert {node["node_key"] for node in queries.list_job_nodes(job["id"])} == set(
        definition.executable_nodes
    )
    assert {node["status"] for node in queries.list_job_nodes(job["id"])} == {"pending"}


def test_upgrade_job_workflow_stages_old_outputs_and_manifest_rows(tmp_path: Path) -> None:
    """#759：clean 升级全量重跑，旧产物文件与权威清单行必须一并失效——
    否则全节点 pending 期间作业仍从对象存储提供上一轮产物，且隐式消费者
    会被旧输入文件立即解锁、读到上一轮结果。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    queries.update_job_node(job["id"], "fetch_items", status="completed")
    queries.update_job_status(job["id"], "completed")
    job_dir = resolve_job_dir(job, tmp_path / "jobs")
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "knowledge_point.json").write_text("stale", encoding="utf-8")
    with queries.connect() as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values (%s, 'fetch_items', 'knowledge_point.json',"
            " 'k/knowledge_point.json', 1, '')",
            (job["id"],),
        )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    assert not (job_dir / "knowledge_point.json").exists()
    with queries.connect() as conn:
        remaining = conn.execute(
            "select name from job_artifacts where job_id=%s", (job["id"],)
        ).fetchall()
    assert remaining == []


def test_upgrade_job_workflow_cancels_queued_requests(tmp_path: Path) -> None:
    """#759：clean 升级整体重建节点集合，必须同事务了结全部 queued 请求。

    能认领旧 payload 的 Worker 离线时，遗留 queued 行不触发任何代次 CAS
    清理，却一直被 has_active_request 视为 active，新 revision 的重派会被
    无限期挡住。取消按 job 作用域（不按节点过滤——旧节点可能已不在新
    定义里）。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    queries.update_job_node(job["id"], "fetch_items", status="completed")
    queries.update_job_status(job["id"], "completed")
    with queries.connect() as conn:
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, node_key,"
            " agent_id, agent_definition_hash, node_concurrency_limit,"
            " state, queued_at, manifest_json)"
            " values ('exec-upgrade-queued', %s, %s, 'fetch_items',"
            " 'generator-v1', 'sha256:whatever', 1, 'queued', current_timestamp, '{}')",
            (workspace["id"], job["id"]),
        )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    with queries.connect() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id='exec-upgrade-queued'"
        ).fetchone()
    assert row["state"] == "cancelled"


def test_upgrade_job_workflow_updates_null_version_job(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    revisions = WorkflowRevisionService(queries)
    revisions.publish_workspace_revision(workspace["id"], definition)
    current = revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "fetch_items", status="completed")
    queries.update_job_status(job["id"], "completed")
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )

    result = service.upgrade(workspace["id"], job["id"])

    upgraded = queries.get_job(job["id"])
    assert result["status"] == "succeeded"
    assert upgraded["workflow_revision_id"] == current["id"]
    assert upgraded["workflow_version"] == current["version"]
    assert upgraded["workflow_definition_hash"] == current["definition_hash"]
    assert upgraded["status"] == "queued"


def test_upgrade_job_workflow_skips_current_revision(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    current = WorkflowRevisionService(queries).publish_workspace_revision(
        workspace["id"], definition
    )
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=list(definition.executable_nodes),
        workspace_id=workspace["id"],
        workflow_revision_id=current["id"],
        workflow_version=current["version"],
        workflow_definition_hash=current["definition_hash"],
        workflow_definition_snapshot_json=current["definition_json"],
    )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "skipped"
    assert result["reason_code"] == "already_current"


def test_upgrade_job_workflow_fails_without_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
    )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "failed"
    assert result["reason_code"] == "no_active_revision"


def test_upgrade_job_workflow_skips_running_job(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    queries.update_job_node(job["id"], "fetch_items", status="running")
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"


def test_upgrade_job_workflow_skips_active_lease(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    run = queries.start_node_run(job["id"], "fetch_items", ["pi"], "")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into executor_leases(id, execution_id, executor_id, workspace_id, job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at) values ('lease-1', 'exec-1', 'pi-1', %s, %s, 'fetch_items', %s, 'active', current_timestamp, current_timestamp, '2999-01-01 00:00:00')
            """,
            (workspace["id"], job["id"], run["id"]),
        )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"


def test_upgrade_job_workflow_fails_for_missing_or_wrong_workspace(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    other_workspace = queries.create_workspace(
        "ws2", default_workflow_key="education_video_problems_generation"
    )
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
    )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )

    missing = service.upgrade(workspace["id"], "missing")
    wrong_workspace = service.upgrade(other_workspace["id"], job["id"])

    assert missing["status"] == "failed"
    assert missing["reason_code"] == "not_found"
    assert wrong_workspace["status"] == "failed"
    assert wrong_workspace["reason_code"] == "not_found"


def test_upgrade_job_workflow_records_event_buffer_update(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    buffer = _RecordingEventBuffer()
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        job_event_buffer=buffer,
    )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    assert buffer.calls == [(workspace["id"], job["id"])]


def test_upgrade_job_workflow_broadcasts_via_event_manager(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch_items"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    manager = _RecordingEventManager()
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        job_event_manager=manager,
    )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    assert len(manager.calls) == 1
    workspace_id, job_id, stats = manager.calls[0]
    assert (workspace_id, job_id) == (workspace["id"], job["id"])
    assert stats == queries.count_jobs_by_status(workspace["id"])


def _batch_setup(tmp_path: Path):
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws1", default_workflow_key="education_video_problems_generation"
    )
    definition = load_builtin_definition("education_video_problems_generation")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    current = revisions.publish_workspace_revision(workspace["id"], definition)

    def _stale_job(source_id: str):
        return queries.create_job(
            workflow_key=definition.key,
            source_type="question",
            source_id=source_id,
            run_id="batch1",
            title=f"Question {source_id}",
            node_keys=["fetch_items"],
            workspace_id=workspace["id"],
            workflow_revision_id=original["id"],
            workflow_version=original["version"],
            workflow_definition_hash=original["definition_hash"],
            workflow_definition_snapshot_json=original["definition_json"],
        )

    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )
    return queries, workspace, current, _stale_job, service


def test_batch_upgrade_upgrades_explicit_ids(tmp_path: Path) -> None:
    queries, workspace, current, make_stale, service = _batch_setup(tmp_path)
    job_a = make_stale("Q1")
    job_b = make_stale("Q2")

    results = batch_upgrade(service, workspace["id"], [job_a["id"], job_b["id"], job_a["id"]])

    assert [r["job_id"] for r in results] == [job_a["id"], job_b["id"]]
    assert all(r["status"] == "succeeded" for r in results)
    for job in (job_a, job_b):
        assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]


def test_batch_upgrade_resolves_filter_and_exclusions(tmp_path: Path) -> None:
    queries, workspace, current, make_stale, service = _batch_setup(tmp_path)
    job_a = make_stale("Q1")
    excluded = make_stale("Q2")

    results = batch_upgrade(
        service,
        workspace["id"],
        job_filter=JobListFilter(status="pending"),
        exclude_ids=[excluded["id"]],
    )

    assert [r["job_id"] for r in results] == [job_a["id"]]
    assert results[0]["status"] == "succeeded"
    assert queries.get_job(job_a["id"])["workflow_revision_id"] == current["id"]
    assert queries.get_job(excluded["id"])["workflow_revision_id"] != current["id"]


def test_batch_upgrade_reports_per_job_skips(tmp_path: Path) -> None:
    _, workspace, current, make_stale, service = _batch_setup(tmp_path)
    stale = make_stale("Q1")
    already_current = make_stale("Q2")
    service.upgrade(workspace["id"], already_current["id"])

    results = batch_upgrade(
        service, workspace["id"], [stale["id"], already_current["id"], "missing"]
    )

    by_id = {r["job_id"]: r for r in results}
    assert by_id[stale["id"]]["status"] == "succeeded"
    assert by_id[already_current["id"]]["status"] == "skipped"
    assert by_id[already_current["id"]]["reason_code"] == "already_current"
    assert by_id["missing"]["status"] == "failed"
    assert by_id["missing"]["reason_code"] == "not_found"
    assert stale["workflow_revision_id"] != current["id"]


def test_batch_upgrade_raises_on_empty_selection(tmp_path: Path) -> None:
    _, workspace, _, _, service = _batch_setup(tmp_path)

    with pytest.raises(EmptyJobSelectionError):
        batch_upgrade(service, workspace["id"], [])
    with pytest.raises(EmptyJobSelectionError):
        batch_upgrade(service, workspace["id"], job_filter=JobListFilter(status="failed"))


def _config_definition(config_schema: dict | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="wf",
        label="Wf",
        intake=WorkflowIntake(),
        nodes={
            "fetch": WorkflowNode(
                key="fetch",
                label="Fetch",
                capability="fetch",
                config_schema=config_schema or {},
            )
        },
    )


def _config_setup(tmp_path: Path, config_schema: dict | None = None):
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="wf")
    current = WorkflowRevisionService(queries).publish_workspace_revision(
        workspace["id"], _config_definition(config_schema)
    )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )
    return queries, workspace, current, service


def test_upgrade_job_workflow_refreshes_stale_snapshot_on_current_revision(
    tmp_path: Path,
) -> None:
    # A job pinning the active revision id but carrying a stale snapshot must
    # be re-pinned, not skipped: dispatch resolves from the snapshot.
    queries, workspace, current, service = _config_setup(tmp_path)
    job = queries.create_job(
        workflow_key="wf",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch"],
        workspace_id=workspace["id"],
        workflow_revision_id=current["id"],
        workflow_version=current["version"],
        workflow_definition_hash="stale-hash",
        workflow_definition_snapshot_json="{}",
    )
    queries.update_job_status(job["id"], "failed")

    result = service.upgrade(workspace["id"], job["id"])

    upgraded = queries.get_job(job["id"])
    assert result["status"] == "succeeded"
    assert upgraded["workflow_revision_id"] == current["id"]
    assert upgraded["workflow_definition_hash"] == current["definition_hash"]
    assert upgraded["workflow_definition_snapshot_json"] == current["definition_json"]
    assert upgraded["status"] == "queued"


def test_upgrade_job_workflow_compares_snapshot_content_not_hash_column(
    tmp_path: Path,
) -> None:
    # The hash column and the snapshot have no consistency constraint: a row
    # can carry the active hash with stale snapshot content. The skip check
    # must compare the snapshot itself, or such rows stay broken forever.
    queries, workspace, current, service = _config_setup(tmp_path)
    job = queries.create_job(
        workflow_key="wf",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch"],
        workspace_id=workspace["id"],
        workflow_revision_id=current["id"],
        workflow_version=current["version"],
        workflow_definition_hash=current["definition_hash"],
        workflow_definition_snapshot_json="{}",
    )
    queries.update_job_status(job["id"], "failed")

    result = service.upgrade(workspace["id"], job["id"])

    upgraded = queries.get_job(job["id"])
    assert result["status"] == "succeeded"
    assert upgraded["workflow_definition_snapshot_json"] == current["definition_json"]


def test_upgrade_job_workflow_reresolves_frozen_node_config(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {"bank_version": {"type": "string", "default": "v5"}},
    }
    queries, workspace, current, service = _config_setup(tmp_path, schema)
    job = queries.create_job(
        workflow_key="wf",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch"],
        workspace_id=workspace["id"],
        workflow_revision_id=current["id"],
        workflow_version=current["version"],
        workflow_definition_hash="stale-hash",
        workflow_definition_snapshot_json="{}",
    )
    queries.update_job_status(job["id"], "failed")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update jobs set frozen_config_json=%s where id=%s",
            (json.dumps({"fetch": {"connection": "cms-internal"}}), job["id"]),
        )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    frozen = json.loads(queries.get_job(job["id"])["frozen_config_json"])
    # Re-resolved as intake would on the active revision: schema defaults
    # (plus platform-reserved execution keys) replace the stale freeze.
    assert frozen["fetch"]["bank_version"] == "v5"
    assert "connection" not in frozen["fetch"]


def test_upgrade_job_workflow_fails_on_invalid_node_config_without_mutation(
    tmp_path: Path,
) -> None:
    schema = {
        "type": "object",
        "properties": {"bank_version": {"type": "string", "default": "v5"}},
    }
    queries, workspace, current, service = _config_setup(tmp_path, schema)
    queries.update_workspace(workspace["id"], node_config={"wf": {"fetch": {"unknown_key": "x"}}})
    job = queries.create_job(
        workflow_key="wf",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["fetch"],
        workspace_id=workspace["id"],
        workflow_revision_id=current["id"],
        workflow_version=current["version"],
        workflow_definition_hash="stale-hash",
        workflow_definition_snapshot_json="{}",
    )
    queries.update_job_status(job["id"], "failed")

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "failed"
    assert result["reason_code"] == "invalid_node_config"
    upgraded = queries.get_job(job["id"])
    assert upgraded["workflow_definition_hash"] == "stale-hash"
    assert upgraded["status"] == "failed"


class _FailingObjectStore:
    """#759 P1 fault injection：post-commit 对象清理的每次调用都抛错。"""

    enabled = True

    def __init__(self) -> None:
        self.probes = 0

    def live_keys_for(self, job_id: str, keys: list[str]) -> set[str]:
        self.probes += 1
        raise RuntimeError("object store is down")

    def delete_objects(self, rows: list[dict]) -> None:
        raise AssertionError("unreachable: the batch probe already failed")


def test_upgrade_post_commit_cleanup_failure_still_reports_success(tmp_path: Path) -> None:
    """#759 P1：DB 已提交后对象清理抛错不得反转结果——返回 succeeded、
    job 已 pin 到新 revision、清单行已在事务内删除。

    突变自检锚点：无兜底的实现会让 store 的 RuntimeError 冒出 upgrade()
    （单任务路由 500），本用例变红。
    """
    from server.app.services.job_artifact_mutation import JobArtifactMutationService

    queries, workspace, current, make_stale, _ = _batch_setup(tmp_path)
    job = make_stale("Q1")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'fetch_items', 'legacy.json', %s, 1, 'hash')
            """,
            (job["id"], f"jobs/{workspace['id']}/{job['id']}/legacy.json"),
        )
    store = _FailingObjectStore()
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
        object_store=store,
    )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    # 清理确实触达了故障 store（deleted_rows 非空，不是空清理假绿）。
    assert store.probes == 1
    upgraded = queries.get_job(job["id"])
    assert upgraded["workflow_revision_id"] == current["id"]
    # 清单行删除是事务内的：post-commit 清理失败不影响已提交结果。
    assert queries.job_artifact_manifest_names_for_nodes(job["id"], {"fetch_items"}) == set()


def test_batch_upgrade_isolates_per_job_failures(tmp_path: Path, monkeypatch) -> None:
    """#759 P1：单 job 的意外异常归一化为该 job 的 failed 结果项
    （reason_code=upgrade_failed），不中断整批、不丢已处理 job 的结果。

    突变自检锚点：无 per-job try/except 的实现会让第一个 job 的异常直接
    冒出 batch_upgrade，后续 job 的结果丢失，本用例变红。
    """
    queries, workspace, current, make_stale, service = _batch_setup(tmp_path)
    job_a = make_stale("Q1")
    job_b = make_stale("Q2")
    real_upgrade = service.upgrade

    def flaky_upgrade(workspace_id, job_id, *, mode="clean"):
        if job_id == job_a["id"]:
            raise RuntimeError("unexpected boom")
        return real_upgrade(workspace_id, job_id, mode=mode)

    monkeypatch.setattr(service, "upgrade", flaky_upgrade)

    results = batch_upgrade(service, workspace["id"], [job_a["id"], job_b["id"]])

    assert [r["job_id"] for r in results] == [job_a["id"], job_b["id"]]
    by_id = {r["job_id"]: r for r in results}
    assert by_id[job_a["id"]]["status"] == "failed"
    assert by_id[job_a["id"]]["reason_code"] == "upgrade_failed"
    assert "unexpected boom" in by_id[job_a["id"]]["message"]
    assert by_id[job_b["id"]]["status"] == "succeeded"
    assert by_id[job_b["id"]]["mode"] == "clean"
    assert queries.get_job(job_b["id"])["workflow_revision_id"] == current["id"]

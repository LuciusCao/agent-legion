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
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
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
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
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
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
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
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
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
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
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
    with closing(connect_database(queries.path)) as conn, conn:
        conn.execute(
            """
            insert into executor_leases(
              id, execution_id, executor_id, workspace_id, job_id, workflow_key,
              node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
            ) values (
              'lease-1', 'exec-1', 'pi-1', %s, %s, %s, 'fetch_items', %s,
              'active', current_timestamp, current_timestamp, '2999-01-01 00:00:00'
            )
            """,
            (workspace["id"], job["id"], definition.key, run["id"]),
        )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
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
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
    )

    missing = service.upgrade(workspace["id"], "missing")
    wrong_workspace = service.upgrade(other_workspace["id"], job["id"])

    assert missing["status"] == "failed"
    assert missing["reason_code"] == "not_found"
    assert wrong_workspace["status"] == "failed"
    assert wrong_workspace["reason_code"] == "wrong_workspace"


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
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
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
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
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
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
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

from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
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
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    current = revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=["fetch_questions"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    queries.update_job_node(job["id"], "fetch_questions", status="completed")
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
    assert {node["node_key"] for node in queries.list_job_nodes(job["id"])} == set(definition.nodes)
    assert {node["status"] for node in queries.list_job_nodes(job["id"])} == {"pending"}


def test_upgrade_job_workflow_updates_null_version_job(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")
    revisions = WorkflowRevisionService(queries)
    revisions.publish_workspace_revision(workspace["id"], definition)
    current = revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=["fetch_questions"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "fetch_questions", status="completed")
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
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")
    current = WorkflowRevisionService(queries).publish_workspace_revision(
        workspace["id"], definition
    )
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=list(definition.nodes),
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
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=["fetch_questions"],
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
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=["fetch_questions"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    queries.update_job_node(job["id"], "fetch_questions", status="running")
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries.path, job_db=queries, data_dir=tmp_path),
    )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"


def test_upgrade_job_workflow_skips_active_lease(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=["fetch_questions"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    run = queries.start_node_run(job["id"], "fetch_questions", ["pi"], "")
    with closing(connect_database(queries.path)) as conn, conn:
        conn.execute(
            """
            insert into executor_leases(
              id, execution_id, executor_id, workspace_id, job_id, workflow_key,
              node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
            ) values (
              'lease-1', 'exec-1', 'pi-1', %s, %s, %s, 'fetch_questions', %s,
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
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    other_workspace = queries.create_workspace(
        "ws2", default_workflow_key="question_comprehension_info"
    )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=["fetch_questions"],
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
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=["fetch_questions"],
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
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=["fetch_questions"],
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

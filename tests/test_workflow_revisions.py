from pathlib import Path

from server.app.jobs.queries import JobQueries
from server.app.services.workflow_drafts import (
    validate_workflow_definition,
    validate_workflow_for_publish,
)
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import load_workflow_definition


def test_publish_and_get_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    service = WorkflowRevisionService(queries)

    revision = service.publish_workspace_revision(workspace["id"], definition)
    active = service.get_active(workspace["id"], definition.key)

    assert active["id"] == revision["id"]
    assert active["workspace_id"] == workspace["id"]
    assert active["workflow_key"] == "question_comprehension_info"
    assert active["version"] == 1
    assert active["status"] == "active"
    assert active["definition_hash"]
    assert active["definition_json"]


def test_create_job_stores_workflow_revision_snapshot(tmp_path: Path) -> None:
    queries = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    service = WorkflowRevisionService(queries)
    revision = service.publish_workspace_revision(workspace["id"], definition)

    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
        workflow_revision_id=revision["id"],
        workflow_definition_hash=revision["definition_hash"],
        workflow_definition_snapshot_json=revision["definition_json"],
    )

    assert job["workflow_revision_id"] == revision["id"]
    assert job["workflow_definition_hash"] == revision["definition_hash"]
    assert "fetch_questions" in job["workflow_definition_snapshot_json"]


def test_validate_workflow_definition_rejects_terminal_without_outcome(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
key: bad
label: Bad
schema_version: 2
nodes:
  a:
    label: A
    capability: a
    terminal: {}
edges: []
""",
        encoding="utf-8",
    )

    errors = validate_workflow_definition(path.read_text(encoding="utf-8"))

    assert any("terminal.outcome" in error for error in errors)


def test_publish_validation_reports_missing_executor_binding(tmp_path: Path) -> None:
    queries = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))

    errors = validate_workflow_for_publish(
        definition=definition,
        workspace_id=workspace["id"],
        job_db=queries,
        settings_executor_definitions={},
    )

    assert any("executor binding" in error for error in errors)


def test_failed_publish_validation_preserves_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(tmp_path / "jobs.sqlite", tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    service = WorkflowRevisionService(queries)
    active = service.publish_workspace_revision(workspace["id"], definition)

    errors = validate_workflow_for_publish(
        definition=definition,
        workspace_id=workspace["id"],
        job_db=queries,
        settings_executor_definitions={},
    )

    assert errors
    assert (
        queries.get_active_workflow_revision(workspace["id"], definition.key)["id"] == active["id"]
    )

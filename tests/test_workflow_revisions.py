from pathlib import Path

from fastapi.testclient import TestClient

from server.app.jobs.queries import JobQueries
from server.app.main import create_app
from server.app.services.workflow_drafts import (
    validate_workflow_definition,
    validate_workflow_for_publish,
    workflow_definition_from_yaml_string,
)
from server.app.services.workflow_revision_format import (
    definition_to_yaml,
    workflow_definition_to_response_payload,
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


def test_get_active_workflow_revision_returns_definition_and_yaml(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as client:
        response = client.post(
            "/api/workspaces",
            json={"name": "Studio", "default_workflow_key": "question_comprehension_info"},
        )
        assert response.status_code == 200
        workspace_id = response.json()["workspace"]["id"]

        active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")

    assert active.status_code == 200
    payload = active.json()
    assert payload["revision"]["status"] == "active"
    assert payload["revision"]["version"] == 1
    assert payload["workflow"]["key"] == "question_comprehension_info"
    assert payload["workflow"]["nodes"]
    assert "key: question_comprehension_info" in payload["definition_yaml"]

    definition = workflow_definition_from_yaml_string(payload["definition_yaml"])
    assert definition.key == "question_comprehension_info"
    assert definition.nodes
    assert definition.edges


def test_get_active_workflow_revision_returns_404_for_workspace_without_revision(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    workspace = app.state.job_db.create_workspace(
        "No Revision",
        default_workflow_key="question_comprehension_info",
    )
    with TestClient(app) as client:
        response = client.get(f"/api/workspaces/{workspace['id']}/workflow-revisions/active")

    assert response.status_code == 404
    assert response.json()["detail"] == "No active workflow revision"


def test_definition_to_yaml_upgrades_v1_to_schema_version_2(tmp_path: Path) -> None:
    definition = load_workflow_definition(Path("config/workflows/video_knowledge.yaml"))

    yaml_text = definition_to_yaml(definition)

    assert "schema_version: 2" in yaml_text
    parsed = workflow_definition_from_yaml_string(yaml_text)
    assert parsed.schema_version == 2
    assert parsed.edges


def test_response_payload_includes_terminal_outcome(tmp_path: Path) -> None:
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))

    payload = workflow_definition_to_response_payload(definition)

    terminal_nodes = [node for node in payload["nodes"] if node.get("terminal")]
    assert terminal_nodes
    assert all(node["terminal"]["outcome"] for node in terminal_nodes)

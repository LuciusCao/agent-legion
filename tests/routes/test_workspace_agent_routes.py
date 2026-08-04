from pathlib import Path

from fastapi.testclient import TestClient

from server.app.jobs.queries import JobQueries
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import load_workflow_definition
from tests.postgres_support import TEST_DATABASE_URL


def _publish(client: TestClient, workspace_id: str = "ws-routes") -> None:
    job_db = JobQueries(TEST_DATABASE_URL, Path(client.app.state.settings.jobs_dir))
    job_db.create_workspace(workspace_id)
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, definition)


def test_agent_routes_returns_materialized_routes(client: TestClient) -> None:
    _publish(client)

    resp = client.get("/api/workspaces/ws-routes/agent-routes")

    assert resp.status_code == 200
    routes = resp.json()["routes"]
    by_node = {entry["node_key"]: entry for entry in routes}
    assert set(by_node) == {
        "generate_key_info",
        "review_key_info",
        "generate_possible_errors",
        "review_possible_errors",
        "assess_comprehension_difficulty",
    }
    entry = by_node["generate_key_info"]
    assert entry["workflow_key"] == "question_comprehension_info"
    assert entry["agent_id"] == "question-key-info-v1"
    assert entry["capability"] == "generate_key_info"
    assert entry["agent_skill"] == "question_comprehension_info/generate_key_info"
    assert entry["node_label"]


def test_agent_routes_empty_without_published_revision(client: TestClient) -> None:
    resp = client.get("/api/workspaces/unknown-ws/agent-routes")

    assert resp.status_code == 200
    assert resp.json() == {"routes": []}

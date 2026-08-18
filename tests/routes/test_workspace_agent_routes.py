from pathlib import Path

from fastapi.testclient import TestClient

from server.app.jobs.queries import JobQueries
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_builtin_definition, seed_workspace_agent_definitions
from tests.postgres_support import TEST_DATABASE_URL


def _publish(client: TestClient, workspace_id: str = "ws-routes") -> None:
    job_db = JobQueries(TEST_DATABASE_URL, Path(client.app.state.settings.jobs_dir))
    job_db.create_workspace(
        workspace_id, default_workflow_key="education_video_problems_generation"
    )
    # Agent definitions are workspace-scoped (schema v46): seed the demo
    # agents into this workspace before publishing so routes materialize.
    seed_workspace_agent_definitions(workspace_id)
    definition = load_builtin_definition("education_video_problems_generation")
    WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, definition)


def test_agent_routes_returns_materialized_routes(client: TestClient) -> None:
    _publish(client)

    resp = client.get("/api/workspaces/ws-routes/agent-routes")

    assert resp.status_code == 200
    routes = resp.json()["routes"]
    by_node = {entry["node_key"]: entry for entry in routes}
    assert set(by_node) == {
        "write_script",
        "review_script",
        "generate_questions",
        "review_questions",
    }
    entry = by_node["write_script"]
    assert entry["workflow_key"] == "education_video_problems_generation"
    assert entry["agent_id"] == "example-write-script-v1"
    assert entry["capability"] == "write_script"
    assert entry["agent_skill"] == "education-video-problems-generation/write-script"
    assert entry["node_label"]


def test_agent_routes_empty_without_published_revision(client: TestClient) -> None:
    resp = client.get("/api/workspaces/unknown-ws/agent-routes")

    assert resp.status_code == 200
    assert resp.json() == {"routes": []}

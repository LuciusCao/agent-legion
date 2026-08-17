from pathlib import Path

import pytest

from server.app.jobs.node_limits import replace_workspace_node_limits
from server.app.jobs.queries import JobQueries
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def queries(tmp_path: Path) -> JobQueries:
    db_path = TEST_DATABASE_URL
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return JobQueries(db_path, jobs_dir)


def test_get_workspace_node_limits_empty(queries: JobQueries) -> None:
    workspace = queries.create_workspace("Math", default_workflow_key="demo_workflow")

    assert queries.get_workspace_node_limits(workspace["id"]) == []


def test_replace_node_limits_is_authoritative(queries: JobQueries) -> None:
    workspace = queries.create_workspace("Math", default_workflow_key="demo_workflow")
    queries.update_workspace_configuration(
        workspace["id"],
        name="Math",
        description="",
        default_workflow_key="demo_workflow",
        default_entity="question",
        resource_config={},
        intake_config={},
        node_limits=[
            {
                "workflow_key": "demo_workflow",
                "node_key": "fetch_items",
                "concurrency_limit": 2,
            }
        ],
    )
    queries.update_workspace_configuration(
        workspace["id"],
        name="Math",
        description="",
        default_workflow_key="demo_workflow",
        default_entity="question",
        resource_config={},
        intake_config={},
        node_limits=[],
    )

    assert queries.get_workspace_node_limits(workspace["id"]) == []


def test_replace_node_limits_rollback(queries: JobQueries) -> None:
    workspace = queries.create_workspace("Math", default_workflow_key="demo_workflow")
    original_node_limits = [
        {
            "workflow_key": "demo_workflow",
            "node_key": "fetch_items",
            "concurrency_limit": 2,
        }
    ]
    with queries.connect() as conn:
        replace_workspace_node_limits(conn, workspace["id"], original_node_limits)

    with pytest.raises(RuntimeError), queries.connect() as conn:
        replace_workspace_node_limits(
            conn,
            workspace["id"],
            [
                {
                    "workflow_key": "demo_workflow",
                    "node_key": "fetch_items",
                    "concurrency_limit": 1,
                }
            ],
        )
        raise RuntimeError("caller aborts after the delete")

    assert queries.get_workspace_node_limits(workspace["id"]) == original_node_limits

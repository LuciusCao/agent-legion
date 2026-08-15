from pathlib import Path

import pytest

from server.app.jobs.executor_configuration import replace_workspace_executor_configuration
from server.app.jobs.queries import JobQueries
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def queries(tmp_path: Path) -> JobQueries:
    db_path = TEST_DATABASE_URL
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return JobQueries(db_path, jobs_dir)


def test_get_workspace_executor_configuration_empty(queries: JobQueries) -> None:
    workspace = queries.create_workspace("Math", default_workflow_key="demo_workflow")

    assert queries.get_workspace_executor_configuration(workspace["id"]) == {
        "allocations": [],
        "bindings": [],
        "node_limits": [],
    }


def test_replace_executor_configuration_is_authoritative(queries: JobQueries) -> None:
    workspace = queries.create_workspace("Math", default_workflow_key="demo_workflow")
    queries.replace_workspace_executor_configuration(
        workspace["id"],
        allocations=[{"executor_id": "code-default", "concurrency_limit": 4}],
        bindings=[
            {
                "workflow_key": "demo_workflow",
                "node_key": "fetch_items",
                "executor_id": "code-default",
            }
        ],
        node_limits=[
            {
                "workflow_key": "demo_workflow",
                "node_key": "fetch_items",
                "concurrency_limit": 2,
            }
        ],
    )
    queries.replace_workspace_executor_configuration(
        workspace["id"], allocations=[], bindings=[], node_limits=[]
    )

    assert queries.get_workspace_executor_configuration(workspace["id"]) == {
        "allocations": [],
        "bindings": [],
        "node_limits": [],
    }


def test_replace_executor_configuration_rollback(queries: JobQueries) -> None:
    workspace = queries.create_workspace("Math", default_workflow_key="demo_workflow")
    original_allocations = [{"executor_id": "code-default", "concurrency_limit": 4}]
    original_bindings = [
        {
            "workflow_key": "demo_workflow",
            "node_key": "fetch_items",
            "executor_id": "code-default",
        }
    ]
    original_node_limits = [
        {
            "workflow_key": "demo_workflow",
            "node_key": "fetch_items",
            "concurrency_limit": 2,
        }
    ]
    queries.replace_workspace_executor_configuration(
        workspace["id"],
        allocations=original_allocations,
        bindings=original_bindings,
        node_limits=original_node_limits,
    )

    with pytest.raises(RuntimeError), queries.connect() as conn:
        replace_workspace_executor_configuration(
            conn,
            workspace["id"],
            allocations=[{"executor_id": "pi-default", "concurrency_limit": 8}],
            bindings=[
                {
                    "workflow_key": "demo_workflow",
                    "node_key": "fetch_items",
                    "executor_id": "pi-default",
                }
            ],
            node_limits=[
                {
                    "workflow_key": "demo_workflow",
                    "node_key": "fetch_items",
                    "concurrency_limit": 1,
                }
            ],
        )
        raise RuntimeError("caller aborts after allocation deletion")

    assert queries.get_workspace_executor_configuration(workspace["id"]) == {
        "allocations": [
            {
                "workspace_id": workspace["id"],
                "executor_id": "code-default",
                "concurrency_limit": 4,
            }
        ],
        "bindings": [
            {
                "workflow_key": "demo_workflow",
                "node_key": "fetch_items",
                "executor_id": "code-default",
            }
        ],
        "node_limits": [
            {
                "workflow_key": "demo_workflow",
                "node_key": "fetch_items",
                "concurrency_limit": 2,
            }
        ],
    }

from pathlib import Path

import pytest

from server.app.jobs.executor_configuration import replace_workspace_executor_configuration
from server.app.jobs.queries import JobQueries


@pytest.fixture
def queries(tmp_path: Path) -> JobQueries:
    db_path = tmp_path / "jobs.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return JobQueries(db_path, jobs_dir)


def test_get_workspace_executor_configuration_empty(queries: JobQueries) -> None:
    workspace = queries.create_workspace("Math", default_workflow_key="question_comprehension_info")

    assert queries.get_workspace_executor_configuration(workspace["id"]) == {
        "allocations": [],
        "bindings": [],
        "node_limits": [],
    }


def test_replace_executor_configuration_is_authoritative(queries: JobQueries) -> None:
    workspace = queries.create_workspace("Math", default_workflow_key="question_comprehension_info")
    queries.replace_workspace_executor_configuration(
        workspace["id"],
        allocations=[{"executor_id": "local-default", "concurrency_limit": 4}],
        bindings=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "fetch_questions",
                "executor_id": "local-default",
            }
        ],
        node_limits=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "fetch_questions",
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
    workspace = queries.create_workspace("Math", default_workflow_key="question_comprehension_info")
    original_allocations = [{"executor_id": "local-default", "concurrency_limit": 4}]
    original_bindings = [
        {
            "workflow_key": "question_comprehension_info",
            "node_key": "fetch_questions",
            "executor_id": "local-default",
        }
    ]
    original_node_limits = [
        {
            "workflow_key": "question_comprehension_info",
            "node_key": "fetch_questions",
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
                    "workflow_key": "question_comprehension_info",
                    "node_key": "fetch_questions",
                    "executor_id": "pi-default",
                }
            ],
            node_limits=[
                {
                    "workflow_key": "question_comprehension_info",
                    "node_key": "fetch_questions",
                    "concurrency_limit": 1,
                }
            ],
        )
        raise RuntimeError("caller aborts after allocation deletion")

    assert queries.get_workspace_executor_configuration(workspace["id"]) == {
        "allocations": [
            {
                "workspace_id": workspace["id"],
                "executor_id": "local-default",
                "concurrency_limit": 4,
            }
        ],
        "bindings": [
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "fetch_questions",
                "executor_id": "local-default",
            }
        ],
        "node_limits": [
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "fetch_questions",
                "concurrency_limit": 2,
            }
        ],
    }

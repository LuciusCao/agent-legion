from pathlib import Path
from types import SimpleNamespace

from server.app.jobs.queries import JobQueries
from server.app.services.workflow_draft_publish import publish_workflow_draft
from tests.postgres_support import TEST_DATABASE_URL

_DRAFT_YAML = """
key: test_publish_flow
label: Test Publish Flow
nodes:
  do_thing:
    capability: do_thing
"""


def _workspace(queries: JobQueries) -> dict:
    return queries.create_workspace("draft-publish-ws", default_workflow_key="test_publish_flow")


def test_publish_rejects_invalid_definition(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    ok, errors = publish_workflow_draft(queries, workspace["id"], "key: only-key\n", {})

    assert ok is False
    assert errors
    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is None


def test_publish_rejects_unresolvable_capability(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    ok, errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML, {})

    assert ok is False
    assert any("do_thing" in error for error in errors)
    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is None


def test_publish_creates_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    queries.replace_workspace_executor_configuration(
        workspace["id"],
        allocations=[{"executor_id": "code-default", "concurrency_limit": 1}],
        bindings=[
            {
                "workflow_key": "test_publish_flow",
                "node_key": "do_thing",
                "executor_id": "code-default",
            }
        ],
        node_limits=[],
    )

    ok, errors = publish_workflow_draft(
        queries,
        workspace["id"],
        _DRAFT_YAML,
        {"code-default": SimpleNamespace(capabilities=["do_thing"])},
    )

    assert (ok, errors) == (True, [])
    active = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    assert active is not None
    assert active["status"] == "active"

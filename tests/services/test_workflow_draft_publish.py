from pathlib import Path

import pytest

from server.app.jobs.queries import JobQueries
from server.app.services.job_errors import DraftWorkflowKeyMismatchError, NotFoundError
from server.app.services.node_codes import NodeCodeService
from server.app.services.workflow_draft_key import require_draft_workflow_key_match
from server.app.services.workflow_draft_publish import (
    publish_workflow_draft,
    validate_workflow_draft_for_publish,
)
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


def _seed_node_code(workspace_id: str) -> None:
    """Publish a no-op workspace node code so the draft node is runnable."""
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(
        workspace_id,
        "test_publish_flow",
        "do_thing",
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish(workspace_id, "test_publish_flow", "do_thing")


def test_publish_rejects_invalid_definition(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    ok, errors = publish_workflow_draft(queries, workspace["id"], "key: only-key\n")

    assert ok is False
    assert errors
    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is None


def test_publish_rejects_unresolvable_capability(tmp_path: Path) -> None:
    """P-0.5: a non-Agent-routed node without published code cannot publish."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    ok, errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)

    assert ok is False
    assert any("do_thing" in error for error in errors)
    assert any("no published node code" in error for error in errors)
    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is None


def test_publish_creates_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])

    ok, errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)

    assert (ok, errors) == (True, [])
    active = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    assert active is not None
    assert active["status"] == "active"


def test_validate_matches_publish_error_set(tmp_path: Path) -> None:
    """validate returns exactly the errors publish would report (前置一致)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    validate_errors = validate_workflow_draft_for_publish(
        queries, workspace["id"], _DRAFT_YAML, True
    )
    ok, publish_errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)

    assert ok is False
    assert validate_errors == publish_errors
    assert any("no published node code" in error for error in validate_errors)
    # Validation is read-only: no revision materialized.
    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is None


def test_validate_clean_with_published_node_code(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])

    assert validate_workflow_draft_for_publish(queries, workspace["id"], _DRAFT_YAML, True) == []


def test_validate_reports_structural_errors_before_bindings(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    errors = validate_workflow_draft_for_publish(queries, workspace["id"], "key: only-key\n", True)

    assert errors


def test_key_match_guard_passes_for_matching_key(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    require_draft_workflow_key_match(queries, workspace["id"], _DRAFT_YAML)


def test_key_match_guard_rejects_foreign_key(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    with pytest.raises(DraftWorkflowKeyMismatchError):
        require_draft_workflow_key_match(
            queries, workspace["id"], _DRAFT_YAML.replace("test_publish_flow", "foreign_flow")
        )


def test_key_match_guard_ignores_unparseable_yaml(tmp_path: Path) -> None:
    """Structural errors stay with the publish validation set, not the guard."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)

    require_draft_workflow_key_match(queries, workspace["id"], "key: [unclosed\n")


def test_key_match_guard_unknown_workspace_raises_not_found(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")

    with pytest.raises(NotFoundError):
        require_draft_workflow_key_match(queries, "no_such_workspace", _DRAFT_YAML)

from pathlib import Path

import pytest

from server.app.jobs.queries import JobQueries
from server.app.services.workflow_draft_publish import publish_workflow_draft
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.postgres_support import TEST_DATABASE_URL
from tests.services.workflow_draft_publish_testlib import (
    _DRAFT_YAML,
    _seed_node_code,
    _workspace,
)

# --- #1226 codex P1: the delete is conditional on the draft's identity hash ---


def _rewrite_draft_before_revision_write(
    monkeypatch: pytest.MonkeyPatch, queries: JobQueries, workspace_id: str, yaml: str
) -> None:
    """Interleave: validation passed on the submitted YAML; before the
    revision write (and its draft-delete hook) land, another tab/agent saves
    ``yaml`` into the draft store."""
    real_save = WorkflowRevisionService.save_workspace_revision

    def rewrite_then_save(self, ws_id, definition, on_commit=None, created_by=""):
        queries.upsert_workspace_workflow_draft(ws_id, yaml)
        return real_save(self, ws_id, definition, on_commit, created_by=created_by)

    monkeypatch.setattr(WorkflowRevisionService, "save_workspace_revision", rewrite_then_save)


def test_publish_with_concurrent_newer_draft_keeps_it(tmp_path: Path, monkeypatch) -> None:
    """#1226 P1: a draft saved AFTER the publish's validation (different
    content) must survive the publish — the delete only fires while the
    stored draft still matches the published content's identity hash. The
    publish itself still succeeds: the newer edit stays as the unpublished
    draft (no 409, no abort)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])
    queries.upsert_workspace_workflow_draft(workspace["id"], _DRAFT_YAML)
    newer = _DRAFT_YAML + "    label: 并发改写\n"
    _rewrite_draft_before_revision_write(monkeypatch, queries, workspace["id"], newer)

    ok, errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)

    assert (ok, errors) == (True, [])
    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is not None
    draft = queries.get_workspace_workflow_draft(workspace["id"])
    assert draft is not None
    assert draft["definition_yaml"] == newer


def test_publish_with_concurrent_identical_redraft_still_deletes(
    tmp_path: Path, monkeypatch
) -> None:
    """#1226 P1: a concurrent save of the SAME content (only updated_at
    advanced — e.g. the canvas's trailing debounce PUT) still deletes: the
    condition is the identity hash, not the timestamp."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])
    queries.upsert_workspace_workflow_draft(workspace["id"], _DRAFT_YAML)
    _rewrite_draft_before_revision_write(monkeypatch, queries, workspace["id"], _DRAFT_YAML)

    ok, errors = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)

    assert (ok, errors) == (True, [])
    assert queries.get_workspace_workflow_draft(workspace["id"]) is None


def test_runtime_only_publish_with_concurrent_newer_draft_keeps_it(
    tmp_path: Path, monkeypatch
) -> None:
    """#1226 P1 on the second write site: the runtime-only in-place edit's
    delete is conditional the same way."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])
    ok, _ = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)
    assert ok
    runtime_edit = _DRAFT_YAML + "    execution:\n      model: glm-4.7\n"
    queries.upsert_workspace_workflow_draft(workspace["id"], runtime_edit)
    newer = runtime_edit + "    label: 并发改写\n"
    _rewrite_draft_before_revision_write(monkeypatch, queries, workspace["id"], newer)

    ok, errors = publish_workflow_draft(queries, workspace["id"], runtime_edit)

    assert (ok, errors) == (True, [])
    active = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    assert active["version"] == 1
    assert "glm-4.7" in str(active["definition_json"])
    draft = queries.get_workspace_workflow_draft(workspace["id"])
    assert draft is not None
    assert draft["definition_yaml"] == newer


def test_runtime_only_publish_aborts_when_active_revision_moves(
    tmp_path: Path, monkeypatch
) -> None:
    """#1226 二轮 codex P1: the runtime-only path reads the active revision
    OUTSIDE the implementation-publication lock. A structural publish landing
    in between archives that row — the in-place edit must NOT be written onto
    the archived row (the live v2 would never receive the runtime config and
    the edit would be silently lost). The edit aborts with a 409-family
    conflict instead: the draft row survives and neither revision is touched.
    """
    from server.app.services import runtime_config as runtime_module
    from server.app.services.job_errors import ConflictError

    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])
    ok, _ = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)
    assert ok
    runtime_edit = _DRAFT_YAML + "    execution:\n      model: glm-4.7\n"
    queries.upsert_workspace_workflow_draft(workspace["id"], runtime_edit)

    real_lock = runtime_module.acquire_implementation_publication_lock
    service = WorkflowRevisionService(queries, True)

    def publish_v2_then_lock(conn, ws_id) -> None:
        # The interleave: after the runtime path's lock-free pre-read of v1,
        # a concurrent structural publish archives v1 and creates v2.
        structural = workflow_definition_from_yaml_string(
            _DRAFT_YAML + "    outputs: [output.json]\n"
        )
        service.publish_workspace_revision(ws_id, structural)
        real_lock(conn, ws_id)

    monkeypatch.setattr(
        runtime_module, "acquire_implementation_publication_lock", publish_v2_then_lock
    )

    with pytest.raises(ConflictError):
        publish_workflow_draft(queries, workspace["id"], runtime_edit)

    # Nothing was written: the draft row survives (the runtime edit is still
    # an unpublished edit against the new active)...
    draft = queries.get_workspace_workflow_draft(workspace["id"])
    assert draft is not None
    assert draft["definition_yaml"] == runtime_edit
    # ...the live v2 never received the runtime config...
    active = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    assert active["version"] == 2
    assert "glm-4.7" not in str(active["definition_json"])
    # ...and the archived v1 row was not rewritten either.
    revisions = [r for r in queries.list_workflow_revisions(workspace["id"], "test_publish_flow")]
    v1 = next(r for r in revisions if r["version"] == 1)
    assert "glm-4.7" not in str(v1["definition_json"])


def test_runtime_only_publish_needs_only_one_pool_connection(tmp_path, monkeypatch) -> None:
    """#1226 三轮 codex P1: the lock-time active-revision revalidation must
    ride the outer transaction's connection. A second pool checkout while the
    outer one is held starves a size-1 pool (pool timeout / failure) — this
    workspace's pool is forced to ``AGENT_LEGION_DB_POOL_MAX_SIZE=1`` via a
    distinct DSN (pools key on (pid, dsn)), so any nested checkout inside the
    runtime-only publish fails instead of silently passing on a big pool."""
    monkeypatch.setenv("AGENT_LEGION_DB_POOL_MAX_SIZE", "1")
    separator = "&" if "?" in TEST_DATABASE_URL else "?"
    dsn = f"{TEST_DATABASE_URL}{separator}application_name=runtime_publish_pool1"
    queries = JobQueries(dsn, tmp_path / "jobs")
    workspace = _workspace(queries)
    _seed_node_code(workspace["id"])
    ok, _ = publish_workflow_draft(queries, workspace["id"], _DRAFT_YAML)
    assert ok
    runtime_edit = _DRAFT_YAML + "    execution:\n      model: glm-4.7\n"
    queries.upsert_workspace_workflow_draft(workspace["id"], runtime_edit)

    ok, errors = publish_workflow_draft(queries, workspace["id"], runtime_edit)

    assert (ok, errors) == (True, [])
    active = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    assert active["version"] == 1
    assert "glm-4.7" in str(active["definition_json"])
    # The draft removal still happened (single-connection flow intact).
    assert queries.get_workspace_workflow_draft(workspace["id"]) is None

"""DB-backed workflow catalog: seeding, registration, workspace binding."""

from __future__ import annotations

import pytest

from server.app.db.transaction import write_transaction
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_catalog_store import (
    WorkflowCatalogStore,
    seed_builtin_workflow_catalog,
)
from server.app.services.workflow_draft_publish import publish_workflow_draft
from server.app.services.workspace_configuration import WorkspaceConfigurationService
from tests.postgres_support import TEST_DATABASE_URL

_REGISTERED_KEY = "acme_quiz_flow"

_DRAFT_YAML = """
key: acme_quiz_flow
label: Acme Quiz Flow
nodes:
  generate:
    capability: generate_key_info
"""


@pytest.fixture
def catalog(settings):
    return WorkflowCatalogService(settings)


@pytest.fixture
def workspace_service(job_db, settings, agent_manager):
    return WorkspaceConfigurationService(
        job_db, settings, agent_manager, WorkflowCatalogService(settings)
    )


def test_seed_is_idempotent_and_refreshes_builtin_rows() -> None:
    seed_builtin_workflow_catalog(TEST_DATABASE_URL)
    seed_builtin_workflow_catalog(TEST_DATABASE_URL)
    entries = WorkflowCatalogStore(TEST_DATABASE_URL).list_entries()
    assert {entry["key"] for entry in entries} == {
        "question_comprehension_info",
        "video_knowledge",
    }
    # A stale builtin row (older code seed) is refreshed on the next seed run.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("update workflow_catalog set label='stale' where key='video_knowledge'")
    seed_builtin_workflow_catalog(TEST_DATABASE_URL)
    entry = WorkflowCatalogStore(TEST_DATABASE_URL).get_entry("video_knowledge")
    assert entry is not None
    assert entry["label"] == "知识视频 DAG"


def test_seed_never_touches_registered_rows(catalog) -> None:
    catalog.register(_REGISTERED_KEY, "Acme Quiz", description="custom")
    seed_builtin_workflow_catalog(TEST_DATABASE_URL)
    entry = WorkflowCatalogStore(TEST_DATABASE_URL).get_entry(_REGISTERED_KEY)
    assert entry is not None
    assert (entry["origin"], entry["label"], entry["definition_json"]) == (
        "registered",
        "Acme Quiz",
        None,
    )


def test_register_rejects_invalid_keys(catalog) -> None:
    for bad_key in ("", "1abc", "Has Space", "Upper", "a" * 65, "with-dash"):
        with pytest.raises(InvalidOperationError):
            catalog.register(bad_key, "Label")


def test_register_rejects_empty_label(catalog) -> None:
    with pytest.raises(InvalidOperationError, match="label"):
        catalog.register("valid_key", "  ")


def test_register_rejects_duplicate_and_builtin_keys(catalog) -> None:
    catalog.register(_REGISTERED_KEY, "Acme Quiz")
    with pytest.raises(ConflictError):
        catalog.register(_REGISTERED_KEY, "Acme Quiz Again")
    with pytest.raises(ConflictError):
        catalog.register("question_comprehension_info", "Builtin Hijack")


def test_definition_resolution_by_origin(catalog) -> None:
    catalog.register(_REGISTERED_KEY, "Acme Quiz")
    with pytest.raises(NotFoundError, match="Unknown workflow"):
        catalog.definition("missing")
    with pytest.raises(NotFoundError, match="no published definition"):
        catalog.definition(_REGISTERED_KEY)
    assert catalog.definition("video_knowledge").key == "video_knowledge"
    assert catalog.definition_or_none(_REGISTERED_KEY) is None
    assert catalog.definition_or_none("missing") is None
    with pytest.raises(NotFoundError, match="Unknown workflow"):
        catalog.bound_definition("missing")
    assert catalog.bound_definition(_REGISTERED_KEY) is None
    assert catalog.label_of(_REGISTERED_KEY) == "Acme Quiz"
    assert catalog.label_of("missing") == "missing"


def test_list_includes_registered_workflows(catalog) -> None:
    catalog.register(_REGISTERED_KEY, "Acme Quiz", description="custom")
    summaries = {entry["key"]: entry for entry in catalog.list_workflows()}
    assert summaries[_REGISTERED_KEY]["origin"] == "registered"
    assert summaries[_REGISTERED_KEY]["description"] == "custom"
    assert summaries["video_knowledge"]["origin"] == "builtin"


def test_workspace_create_with_registered_key_defers_revision(
    catalog, workspace_service, job_db
) -> None:
    catalog.register(_REGISTERED_KEY, "Acme Quiz")
    workspace = workspace_service.create({"name": "Acme", "default_workflow_key": _REGISTERED_KEY})
    assert workspace["default_workflow_key"] == _REGISTERED_KEY
    assert job_db.get_active_workflow_revision(workspace["id"], _REGISTERED_KEY) is None


def test_workspace_create_with_unknown_key_still_rejected(workspace_service) -> None:
    with pytest.raises(NotFoundError, match="Unknown workflow"):
        workspace_service.create({"name": "Nope", "default_workflow_key": "missing"})


def test_first_draft_publish_creates_revision_for_registered_key(
    catalog, workspace_service, job_db
) -> None:
    catalog.register(_REGISTERED_KEY, "Acme Quiz")
    workspace = workspace_service.create({"name": "Acme", "default_workflow_key": _REGISTERED_KEY})

    ok, errors = publish_workflow_draft(job_db, workspace["id"], _DRAFT_YAML, {})

    assert (ok, errors) == (True, [])
    active = job_db.get_active_workflow_revision(workspace["id"], _REGISTERED_KEY)
    assert active is not None
    assert active["version"] == 1


def test_replace_configuration_without_executor_payload_works_definitionless(
    catalog, workspace_service
) -> None:
    catalog.register(_REGISTERED_KEY, "Acme Quiz")
    workspace = workspace_service.create({"name": "Acme", "default_workflow_key": _REGISTERED_KEY})

    result = workspace_service.replace_configuration(
        workspace["id"],
        workspace_patch={"name": "Renamed"},
        settings_patch={},
        executor_allocations=[],
        node_bindings=[],
        node_limits=[],
    )

    assert result["workspace"]["name"] == "Renamed"


def test_replace_configuration_rejects_executor_payload_definitionless(
    catalog, workspace_service
) -> None:
    catalog.register(_REGISTERED_KEY, "Acme Quiz")
    workspace = workspace_service.create({"name": "Acme", "default_workflow_key": _REGISTERED_KEY})

    with pytest.raises(InvalidOperationError, match="no published definition"):
        workspace_service.replace_configuration(
            workspace["id"],
            workspace_patch={},
            settings_patch={},
            executor_allocations=[{"executor_id": "code-default", "concurrency_limit": 1}],
            node_bindings=[],
            node_limits=[],
        )

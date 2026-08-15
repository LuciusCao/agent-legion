"""ExecutorDefinitionService: DB-backed executor catalog lifecycle (schema v30)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from server.app.executors.code_config import CodeExecutorConfig
from server.app.executors.kinds import UnknownExecutorKindError
from server.app.services.executor_definition_service import (
    ExecutorDefinitionService,
    seed_builtin_executor_definitions,
)
from server.app.services.job_errors import InvalidOperationError

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_DEFINITION = {
    "kind": "code",
    "global_capacity": 2,
    "capabilities": {"clean_and_parse": {"path": "workflow_nodes/question_clean_parse.py"}},
}


@pytest.fixture
def service(job_db):
    return ExecutorDefinitionService(job_db.path, REPO_ROOT)


def test_conftest_seeds_builtin_catalog(service) -> None:
    published = service.list_published_definitions()
    assert set(published) == {"code-default"}
    config = published["code-default"]
    assert isinstance(config, CodeExecutorConfig)
    # 2 demo workflow capabilities.
    assert len(config.capabilities) == 2


def test_demo_capabilities_bind_builtin_node_paths(service) -> None:
    """The demo workflow's code capabilities bind repo files (EXEC-CODE-001)."""
    config = service.list_published_definitions()["code-default"]
    intake = config.capabilities["intake_knowledge_points"]
    publish = config.capabilities["publish_content"]
    assert str(intake.path) == "workflow_nodes/example_intake.py"
    assert str(publish.path) == "workflow_nodes/example_publish.py"
    properties = intake.config_schema["properties"]
    assert properties["knowledge_dir"]["default"] == (
        "examples/education-video-problems-generation"
    )


def test_save_draft_then_publish_round_trip(service) -> None:
    draft = service.save_draft("code-extra", VALID_DEFINITION, "user:u1")
    assert draft.version == 1
    assert draft.status == "draft"
    assert service.get_published("code-extra") is None

    published = service.publish("code-extra")

    assert published.status == "published"
    assert published.published_at is not None
    definitions = service.list_published_definitions()
    assert set(definitions) == {"code-default", "code-extra"}
    assert definitions["code-extra"].global_capacity == 2


def test_save_draft_rejects_empty_executor_id(service) -> None:
    with pytest.raises(InvalidOperationError):
        service.save_draft("", VALID_DEFINITION, "user:u1")


def test_save_draft_rejects_unknown_kind(service) -> None:
    with pytest.raises(UnknownExecutorKindError):
        service.save_draft(
            "code-bad", {"kind": "quantum", "global_capacity": 1, "capabilities": {}}, "user:u1"
        )


def test_save_draft_rejects_unsafe_path(service) -> None:
    payload = {
        "kind": "code",
        "global_capacity": 1,
        "capabilities": {"x": {"path": "../outside.py"}},
    }
    with pytest.raises(ValidationError):
        service.save_draft("code-bad", payload, "user:u1")


def test_save_draft_rejects_invalid_config_schema(service) -> None:
    payload = {
        "kind": "code",
        "global_capacity": 1,
        "capabilities": {
            "x": {
                "path": "workflow_nodes/question_clean_parse.py",
                "config_schema": {"type": "object", "properties": {"bad": {"type": "nope"}}},
            }
        },
    }
    with pytest.raises(ValidationError):
        service.save_draft("code-bad", payload, "user:u1")


def test_publish_rejects_path_outside_repo(service) -> None:
    payload = {
        "kind": "code",
        "global_capacity": 1,
        "capabilities": {"x": {"path": "workflow_nodes/does_not_exist.py"}},
    }
    service.save_draft("code-bad", payload, "user:u1")

    with pytest.raises(InvalidOperationError, match="publish rejected"):
        service.publish("code-bad")
    assert service.get_published("code-bad") is None


def test_publish_accepts_pathless_custom_code_capability(service) -> None:
    """A capability without a repo path is custom-code only (EXEC-CODE-002)."""
    payload = {
        "kind": "code",
        "global_capacity": 1,
        "capabilities": {"custom_only": {"timeout_seconds": 60}},
    }
    service.save_draft("code-custom", payload, "user:u1")

    published = service.publish("code-custom")

    assert published.status == "published"
    config = service.list_published_definitions()["code-custom"]
    assert config.capabilities["custom_only"].path is None


def test_rollback_accepts_pathless_custom_code_capability(service) -> None:
    payload = {
        "kind": "code",
        "global_capacity": 1,
        "capabilities": {"custom_only": {}},
    }
    service.save_draft("code-custom", payload, "user:u1")
    service.publish("code-custom")
    edited = {
        "kind": "code",
        "global_capacity": 2,
        "capabilities": {"custom_only": {}},
    }
    service.save_draft("code-custom", edited, "user:u1")
    service.publish("code-custom")

    rolled = service.rollback("code-custom", 1, "user:u1")

    assert rolled.status == "published"
    config = service.list_published_definitions()["code-custom"]
    assert config.capabilities["custom_only"].path is None


def test_publish_invalidates_cached_catalog(service) -> None:
    before = service.list_published_definitions()
    assert "code-extra" not in before
    service.save_draft("code-extra", VALID_DEFINITION, "user:u1")
    service.publish("code-extra")

    # The write path invalidated the cache: no TTL wait needed.
    after = service.list_published_definitions()
    assert "code-extra" in after


def test_seed_is_absent_only_and_never_overrides_admin_edits(service) -> None:
    # conftest already seeded the factory catalog; re-seeding is a no-op.
    assert seed_builtin_executor_definitions(service) == []

    # Admin edit: publish a new version of code-default with a lower capacity.
    edited = {
        "kind": "code",
        "global_capacity": 4,
        "capabilities": {"clean_and_parse": {"path": "workflow_nodes/question_clean_parse.py"}},
    }
    service.save_draft("code-default", edited, "user:admin")
    service.publish("code-default")

    assert seed_builtin_executor_definitions(service) == []
    assert service.list_published_definitions()["code-default"].global_capacity == 4


def test_seed_does_not_resurrect_archived_executor(service) -> None:
    service.archive_all("code-default")

    assert seed_builtin_executor_definitions(service) == []
    assert service.list_published_definitions() == {}

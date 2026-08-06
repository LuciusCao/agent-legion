"""AgentService: DB-backed Agent catalog lifecycle on versioned_entities."""

from __future__ import annotations

import json

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_service import AgentService
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from tests.helpers import replace_agent_catalog

DEFINITION_V1 = AgentDefinition(
    capability="review_keywords", runtime="velites", skill="question/review_key_info"
)
DEFINITION_V2 = AgentDefinition(
    capability="review_keywords",
    runtime="velites",
    skill="question/review_key_info",
    tools=("read",),
)


@pytest.fixture
def service(job_db):
    # Isolate from the conftest-seeded catalog: capability-uniqueness checks
    # must only see the Agents this test publishes.
    replace_agent_catalog({})
    return AgentService(job_db.path)


def test_save_draft_then_publish_round_trip(service) -> None:
    draft = service.save_draft("agent-a", DEFINITION_V1, "user:u1")

    assert draft.version == 1
    assert draft.status == "draft"
    assert draft.workspace_id is None
    assert draft.definition_hash == DEFINITION_V1.definition_hash()
    assert service.get_published_definition("agent-a") is None

    published = service.publish("agent-a")

    assert published.status == "published"
    assert published.published_at is not None
    assert service.get_published_definition("agent-a") == DEFINITION_V1


def test_get_published_definition_enforces_hash(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")

    expected = DEFINITION_V1.definition_hash()
    assert service.get_published_definition("agent-a", expected) == DEFINITION_V1
    assert service.get_published_definition("agent-a", "tampered") is None
    assert service.get_published_definition("agent-missing") is None


def test_save_draft_rejects_empty_agent_id(service) -> None:
    with pytest.raises(InvalidOperationError):
        service.save_draft("", DEFINITION_V1, "user:u1")


def test_publish_without_draft_raises(service) -> None:
    with pytest.raises(NotFoundError):
        service.publish("agent-a")


def test_publish_rejects_duplicate_capability(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    service.save_draft("agent-b", DEFINITION_V1, "user:u1")

    with pytest.raises(ConflictError, match="capability"):
        service.publish("agent-b")

    # Archiving the owner frees the capability.
    service.archive_all("agent-a")
    published = service.publish("agent-b")
    assert published.status == "published"


def test_same_capability_same_agent_republishes(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    service.save_draft("agent-a", DEFINITION_V2, "user:u1")

    republished = service.publish("agent-a")

    assert republished.version == 2
    assert service.get_published_definition("agent-a") == DEFINITION_V2
    versions = {e.version: e.status for e in service.list_versions("agent-a")}
    assert versions == {1: "archived", 2: "published"}


def test_rollback_restores_old_definition(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    service.save_draft("agent-a", DEFINITION_V2, "user:u1")
    service.publish("agent-a")

    rolled = service.rollback("agent-a", 1, "user:ops")

    assert rolled.version == 3
    assert rolled.status == "published"
    assert service.get_published_definition("agent-a") == DEFINITION_V1


def test_rollback_unknown_version_raises(service) -> None:
    with pytest.raises(NotFoundError):
        service.rollback("agent-a", 99, "user:ops")


def test_rollback_rejects_duplicate_capability(service) -> None:
    """rollback 与 publish 走同一 capability 冲突检查（防御层）。"""
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    other = AgentDefinition(capability="other_cap", runtime="velites", skill="q/other")
    service.save_draft("agent-a", other, "user:u1")
    service.publish("agent-a")
    # agent-b now owns DEFINITION_V1's capability; rolling agent-a back to v1
    # would collide with it.
    service.save_draft("agent-b", DEFINITION_V1, "user:u1")
    service.publish("agent-b")

    with pytest.raises(ConflictError, match="capability"):
        service.rollback("agent-a", 1, "user:ops")

    # Archiving the owner frees the capability; the rollback then lands.
    service.archive_all("agent-b")
    rolled = service.rollback("agent-a", 1, "user:ops")
    assert rolled.status == "published"
    assert service.get_published_definition("agent-a") == DEFINITION_V1


def test_db_index_rejects_second_published_capability(service, job_db) -> None:
    """DB 层真实 guard：绕过 service 直接写第二行同 capability published 必失败。"""
    from psycopg import IntegrityError

    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")

    with pytest.raises(IntegrityError), job_db.connect() as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('agent:agent-b:v1', 'agent', null, 'agent-b', 1, 'published',"
            " %s, 'hash-b', 'user:test')",
            (json.dumps(DEFINITION_V1.model_dump(mode="json")),),
        )

    # 同一 agent re-publish（先归档旧版再发新版）不撞索引。
    service.save_draft("agent-a", DEFINITION_V2, "user:u1")
    republished = service.publish("agent-a")
    assert republished.version == 2


def test_archive_all_unpublishes(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")

    assert service.archive_all("agent-a") == 1
    assert service.get_published_definition("agent-a") is None
    assert service.archive_all("agent-a") == 0


def test_copy_creates_independent_draft(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")

    copied = service.copy("agent-a", "agent-b", "user:u2")

    assert copied.entity_key == "agent-b"
    assert copied.version == 1
    assert copied.status == "draft"
    assert AgentDefinition.model_validate(copied.definition) == DEFINITION_V1
    # The copy must not trip the capability guard while it stays a draft.
    with pytest.raises(ConflictError, match="capability"):
        service.publish("agent-b")


def test_copy_missing_source_raises(service) -> None:
    with pytest.raises(NotFoundError):
        service.copy("agent-missing", "agent-b", "user:u1")


def test_copy_rejects_empty_new_id(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    with pytest.raises(InvalidOperationError):
        service.copy("agent-a", "", "user:u1")


def test_list_latest_and_published_definitions(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    service.save_draft("agent-a", DEFINITION_V2, "user:u1")
    other = AgentDefinition(capability="generate_key_info", runtime="velites", skill="q/gen")
    service.save_draft("agent-b", other, "user:u1")
    service.publish("agent-b")

    latest = {e.entity_key: e for e in service.list_latest()}
    assert latest["agent-a"].status == "draft"
    assert latest["agent-b"].status == "published"

    published = {d.capability for d in service.list_published_definitions()}
    assert published == {"review_keywords", "generate_key_info"}

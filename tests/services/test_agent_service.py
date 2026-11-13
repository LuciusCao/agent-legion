"""AgentService read-only access to historical versioned_entities rows."""

from __future__ import annotations

import importlib.util
import json

import pytest
from psycopg import IntegrityError

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_service import (
    AgentService,
    has_published_agent_definitions,
    published_agent_definitions,
    reset_published_agent_cache,
)
from server.app.services.job_errors import InvalidOperationError
from server.app.services.versioned_entities import VersionedEntityStore

DEFINITION_V1 = AgentDefinition(
    capability="review_keywords", runtime="velites", skill="question/review_key_info"
)
DEFINITION_V2 = AgentDefinition(
    capability="renamed_cap",
    runtime="velites",
    skill="question/review_key_info",
    tools=("read",),
)


@pytest.fixture
def workspace_id(job_db) -> str:
    return job_db.create_workspace("Agent Service WS")["id"]


@pytest.fixture
def service(job_db, workspace_id) -> AgentService:
    return AgentService(job_db.dsn_identity, workspace_id)


@pytest.fixture
def store(job_db) -> VersionedEntityStore:
    return VersionedEntityStore(job_db.dsn_identity, "agent")


def _save(
    store: VersionedEntityStore,
    workspace_id: str,
    agent_id: str,
    definition: AgentDefinition,
    created_by: str = "test-seed",
):
    return store.save_draft(
        agent_id,
        definition.model_dump(mode="json"),
        definition.definition_hash(),
        workspace_id,
        created_by,
    )


def test_reads_latest_published_versions_and_exact_hash(service, store, workspace_id) -> None:
    saved = _save(store, workspace_id, "agent-a", DEFINITION_V1)
    store.publish("agent-a", workspace_id, saved.definition_hash)
    _save(store, workspace_id, "agent-a", DEFINITION_V2, "user:u2")

    latest = service.list_latest()
    assert len(latest) == 1
    assert latest[0].version == 2
    assert latest[0].status == "draft"
    assert latest[0].created_by == "user:u2"
    assert [entity.version for entity in service.list_published()] == [1]
    assert service.list_published_definitions() == [DEFINITION_V1]
    assert service.get_published("agent-a").definition_hash == DEFINITION_V1.definition_hash()
    assert service.get_published_definition("agent-a") == DEFINITION_V1
    assert (
        service.get_published_definition("agent-a", DEFINITION_V1.definition_hash())
        == DEFINITION_V1
    )
    assert service.get_published_definition("agent-a", "tampered") is None
    assert service.get_published_definition("agent-missing") is None
    assert [entity.version for entity in service.list_versions("agent-a")] == [2, 1]


def test_reads_archived_and_preconstraint_entities(service, store, workspace_id) -> None:
    saved = _save(store, workspace_id, "code:x", DEFINITION_V1)
    store.publish("code:x", workspace_id, saved.definition_hash)
    assert store.archive_all("code:x", workspace_id) == 1

    (latest,) = service.list_latest()
    assert latest.entity_key == "code:x"
    assert latest.status == "archived"
    assert service.list_published() == []
    assert service.get_published("code:x") is None
    assert [entity.status for entity in service.list_versions("code:x")] == ["archived"]


def test_reads_are_workspace_isolated(service, store, job_db, workspace_id) -> None:
    _save(store, workspace_id, "agent-a", DEFINITION_V1)
    other = job_db.create_workspace("Other WS")["id"]

    assert AgentService(job_db.dsn_identity, other).list_latest() == []
    assert AgentService(job_db.dsn_identity, other).list_versions("agent-a") == []
    with pytest.raises(InvalidOperationError):
        AgentService(job_db.dsn_identity, "")


def test_published_catalog_and_probe_read_historical_rows(job_db, store, workspace_id) -> None:
    reset_published_agent_cache()
    assert published_agent_definitions(job_db.dsn_identity, workspace_id) == {}
    assert not has_published_agent_definitions(job_db.dsn_identity)

    saved = _save(store, workspace_id, "agent-a", DEFINITION_V1)
    store.publish("agent-a", workspace_id, saved.definition_hash)
    reset_published_agent_cache()
    assert published_agent_definitions(job_db.dsn_identity, workspace_id) == {
        "agent-a": DEFINITION_V1
    }
    assert has_published_agent_definitions(job_db.dsn_identity)

    store.archive_all("agent-a", workspace_id)
    reset_published_agent_cache()
    assert published_agent_definitions(job_db.dsn_identity, workspace_id) == {}
    assert not has_published_agent_definitions(job_db.dsn_identity)


def test_write_methods_and_modules_are_gone(service) -> None:
    for method in ("save_draft", "publish", "rollback", "archive_all", "copy"):
        assert not hasattr(service, method)
    for module in (
        "server.app.routes.agent_definition_deprecation",
        "server.app.services.agent_definition_create",
        "server.app.services.agent_publish_prune",
    ):
        assert importlib.util.find_spec(module) is None


def test_generic_store_keeps_db_capability_guard(service, store, job_db, workspace_id) -> None:
    saved = _save(store, workspace_id, "agent-a", DEFINITION_V1)
    store.publish("agent-a", workspace_id, saved.definition_hash)
    canonical = json.dumps(DEFINITION_V1.model_dump(mode="json"))

    with pytest.raises(IntegrityError), job_db.connect() as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('agent:agent-b:v1', 'agent', %s, 'agent-b', 1, 'published',"
            " %s, 'hash-b', 'user:test')",
            (workspace_id, canonical),
        )

    other_ws = job_db.create_workspace("Other WS")["id"]
    with job_db.connect() as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('agent:other:agent-b:v1', 'agent', %s, 'agent-b', 1, 'published',"
            " %s, 'hash-b', 'user:test')",
            (other_ws, canonical),
        )

    second = _save(store, workspace_id, "agent-a", DEFINITION_V2)
    store.publish("agent-a", workspace_id, second.definition_hash)
    assert service.get_published_definition("agent-a") == DEFINITION_V2
    assert {entity.version: entity.status for entity in service.list_versions("agent-a")} == {
        1: "archived",
        2: "published",
    }

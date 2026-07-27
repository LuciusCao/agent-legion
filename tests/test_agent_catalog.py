from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.app.agent_catalog import (
    AgentDefinition,
    get_agent_definition,
    load_agent_definitions,
    sync_agent_definitions,
)
from tests.postgres_support import TEST_DATABASE_URL


def test_agent_definition_hash_is_stable_across_mapping_order() -> None:
    first = AgentDefinition.model_validate(
        {
            "capability": "generate_key_info",
            "runtime": "pi",
            "skill": "question/generate_key_info",
            "tools": ["read", "write"],
            "requires_labels": {"arch": "arm64", "device": "mac-mini"},
        }
    )
    second = AgentDefinition.model_validate(
        {
            "requires_labels": {"device": "mac-mini", "arch": "arm64"},
            "tools": ["read", "write"],
            "skill": "question/generate_key_info",
            "runtime": "pi",
            "capability": "generate_key_info",
        }
    )

    assert first.definition_hash() == second.definition_hash()


def test_agent_definition_rejects_unsafe_skill_path() -> None:
    with pytest.raises(ValidationError, match="skill path"):
        AgentDefinition.model_validate(
            {
                "capability": "cap",
                "runtime": "pi",
                "skill": "../outside",
            }
        )


def test_agent_definition_accepts_config_schema_and_hashes_it() -> None:
    with_schema = AgentDefinition.model_validate(
        {
            "capability": "cap",
            "runtime": "pi",
            "skill": "question/generate",
            "config_schema": {"properties": {"page_size": {"type": "integer", "default": 50}}},
        }
    )
    without_schema = AgentDefinition(
        capability="cap",
        runtime="pi",
        skill="question/generate",
    )

    assert with_schema.config_schema["properties"]["page_size"]["default"] == 50
    assert without_schema.config_schema == {}
    assert with_schema.definition_hash() != without_schema.definition_hash()


def test_agent_definition_rejects_invalid_config_schema() -> None:
    with pytest.raises(ValidationError, match="unsupported keys"):
        AgentDefinition.model_validate(
            {
                "capability": "cap",
                "runtime": "pi",
                "skill": "question/generate",
                "config_schema": {"properties": {"x": {"type": "string", "pattern": "^a"}}},
            }
        )


def test_load_agent_definitions_rejects_duplicate_capability() -> None:
    with pytest.raises(ValueError, match="exactly one Agent Definition per capability"):
        load_agent_definitions(
            {
                "generator-v1": {
                    "capability": "generate",
                    "runtime": "pi",
                    "skill": "question/generate",
                },
                "generator-v2": {
                    "capability": "generate",
                    "runtime": "pi",
                    "skill": "question/generate_v2",
                },
            }
        )


def test_load_agent_definitions_accepts_distinct_capabilities() -> None:
    definitions = load_agent_definitions(
        {
            "generator-v1": {
                "capability": "generate",
                "runtime": "pi",
                "skill": "question/generate",
            },
            "reviewer-v1": {
                "capability": "review",
                "runtime": "pi",
                "skill": "question/review",
            },
        }
    )

    assert set(definitions) == {"generator-v1", "reviewer-v1"}
    assert {definition.capability for definition in definitions.values()} == {
        "generate",
        "review",
    }


def test_sync_agent_definitions_replaces_enabled_catalog(job_db) -> None:
    first = AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
    )
    second = AgentDefinition(
        capability="review",
        runtime="openclaw",
        skill="question/review",
    )
    sync_agent_definitions(TEST_DATABASE_URL, {"generator-v1": first, "reviewer-v1": second})

    assert get_agent_definition(TEST_DATABASE_URL, "generator-v1") == first
    assert get_agent_definition(TEST_DATABASE_URL, "generator-v1", first.definition_hash()) == first
    assert get_agent_definition(TEST_DATABASE_URL, "generator-v1", "stale") is None

    sync_agent_definitions(TEST_DATABASE_URL, {"reviewer-v1": second})

    assert get_agent_definition(TEST_DATABASE_URL, "generator-v1") is None
    with job_db.connect() as conn:
        disabled = conn.execute(
            "select enabled from agent_definitions where agent_id=?", ("generator-v1",)
        ).fetchone()
    assert disabled is not None
    assert disabled["enabled"] == 0


def test_sync_agent_definitions_refuses_empty_catalog_over_enabled_rows(job_db) -> None:
    # The autouse fixture already synced the configured catalog (enabled rows exist).
    with pytest.raises(ValueError, match="empty Agent catalog"):
        sync_agent_definitions(TEST_DATABASE_URL, {})

    with job_db.connect() as conn:
        enabled = conn.execute(
            "select count(*) as c from agent_definitions where enabled=1"
        ).fetchone()
    assert enabled["c"] > 0


def test_sync_agent_definitions_allows_empty_catalog_when_nothing_enabled(job_db) -> None:
    with job_db.connect() as conn:
        conn.execute("update agent_definitions set enabled=0")

    sync_agent_definitions(TEST_DATABASE_URL, {})

    with job_db.connect() as conn:
        remaining = conn.execute(
            "select count(*) as c from agent_definitions where enabled=1"
        ).fetchone()
    assert remaining["c"] == 0

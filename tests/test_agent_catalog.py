from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.app.agent_catalog import AgentDefinition


@pytest.mark.no_db
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


@pytest.mark.no_db
def test_agent_definition_rejects_unsafe_skill_path() -> None:
    with pytest.raises(ValidationError, match="skill path"):
        AgentDefinition.model_validate(
            {
                "capability": "cap",
                "runtime": "pi",
                "skill": "../outside",
            }
        )


@pytest.mark.no_db
def test_agent_definition_accepts_velites_runtime() -> None:
    definition = AgentDefinition.model_validate(
        {
            "capability": "cap",
            "runtime": "velites",
            "skill": "question/generate",
        }
    )

    assert definition.runtime == "velites"
    # runtime participates in the immutable snapshot hash (migration => new revision).
    pi_twin = AgentDefinition(capability="cap", runtime="pi", skill="question/generate")
    assert definition.definition_hash() != pi_twin.definition_hash()


@pytest.mark.no_db
def test_agent_definition_rejects_unknown_runtime() -> None:
    with pytest.raises(ValidationError):
        AgentDefinition.model_validate(
            {
                "capability": "cap",
                "runtime": "rust",
                "skill": "question/generate",
            }
        )


@pytest.mark.no_db
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


@pytest.mark.no_db
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

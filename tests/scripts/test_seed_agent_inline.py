"""Legacy seed Agent definitions inline into nodes without any catalog writes."""

from __future__ import annotations

import copy

import pytest

from scripts.seed.seed_agent_inline import inline_agent_profiles

pytestmark = pytest.mark.no_db


def _legacy_agent() -> dict:
    return {
        "agent_id": "legacy-reviewer",
        "version": 7,
        "definition": {
            "capability": "review",
            "runtime": "pi",
            "skill": "acme/review",
            "tools": ["read", "write"],
            "requires_labels": {"hardware": "gpu"},
            "config_schema": {
                "type": "object",
                "properties": {"max_words": {"type": "integer", "default": 800, "minimum": 1}},
            },
        },
    }


def _definition() -> dict:
    return {
        "key": "reviews",
        "label": "Reviews",
        "nodes": {
            "review": {
                "label": "Review",
                "type": "agent",
                "capability": "review",
                "outputs": ["review.json"],
            }
        },
        "edges": [],
    }


def test_legacy_agent_inlines_the_complete_profile_without_mutating_seed() -> None:
    definition = _definition()
    original = copy.deepcopy(definition)

    inlined, untouched = inline_agent_profiles(definition, [_legacy_agent()])

    assert untouched == []
    node = inlined["nodes"]["review"]
    assert node["execution"] == {"runtime": "pi"}
    assert node["tools"] == ["read", "write"]
    assert node["requires_labels"] == {"hardware": "gpu"}
    assert node["config_schema"] == _legacy_agent()["definition"]["config_schema"]
    assert node["skill"] == {"key": "acme/review", "ref": "latest"}
    assert definition == original


def test_revision_shaped_legacy_node_receives_the_same_complete_profile() -> None:
    definition = _definition()
    definition["nodes"]["review"]["node_type"] = definition["nodes"]["review"].pop("type")

    inlined, untouched = inline_agent_profiles(definition, [_legacy_agent()])

    assert untouched == []
    node = inlined["nodes"]["review"]
    assert node["execution"] == {"runtime": "pi"}
    assert node["tools"] == ["read", "write"]
    assert node["requires_labels"] == {"hardware": "gpu"}
    assert node["config_schema"] == _legacy_agent()["definition"]["config_schema"]
    assert node["skill"] == {"key": "acme/review", "ref": "latest"}


def test_workflow_default_does_not_overwrite_an_effective_node_profile() -> None:
    definition = _definition()
    node = definition["nodes"]["review"]
    node.update(
        tools=["bash"],
        config_schema={"type": "object", "properties": {"own": {"type": "string"}}},
        skill={"key": "acme/own", "ref": "v2"},
    )
    definition["execution"] = {"runtime": "velites"}

    inlined, untouched = inline_agent_profiles(definition, [_legacy_agent()])

    assert untouched == []
    assert inlined["nodes"]["review"] == node
    assert inlined["execution"] == {"runtime": "velites"}

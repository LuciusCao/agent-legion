from __future__ import annotations

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.config_schema import ConfigSchemaError
from server.app.executors.config import LocalCapabilityConfig, LocalExecutorConfig
from server.app.services.node_config import (
    capability_config_schemas,
    dispatch_effective_config,
    executor_capability_config_schemas,
    executor_definition_capability_schema,
    frozen_node_config,
    resolve_node_config,
    resolve_workflow_node_configs,
    workflow_node_config_schemas,
    workspace_node_overrides,
)
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode

SCHEMA = {
    "type": "object",
    "properties": {
        "page_size": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
        "subject_id": {"type": "string", "enum": ["math", "physics"]},
        "api_key": {"type": "string", "secret": True},
    },
}


def _agent(capability: str = "generate", schema: dict | None = None) -> AgentDefinition:
    return AgentDefinition(
        capability=capability,
        runtime="pi",
        skill="question/generate",
        config_schema=schema or {},
    )


def _definition(node_config: dict | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="wf",
        label="Wf",
        intake=WorkflowIntake(),
        nodes={
            "generate": WorkflowNode(
                key="generate",
                label="Generate",
                capability="generate",
                config=node_config or {},
            )
        },
    )


def test_capability_schemas_skip_agents_without_schema() -> None:
    agents = {"a": _agent("generate", SCHEMA), "b": _agent("review")}
    assert capability_config_schemas(agents) == {"generate": SCHEMA}


def test_resolve_node_config_chain_priority() -> None:
    effective = resolve_node_config(SCHEMA, {"page_size": 20}, {"page_size": 30})
    assert effective == {"page_size": 30}
    effective = resolve_node_config(SCHEMA, {"subject_id": "math"}, {})
    assert effective == {"page_size": 50, "subject_id": "math"}


def test_resolve_node_config_rejects_invalid_layers() -> None:
    with pytest.raises(ConfigSchemaError, match="unknown keys"):
        resolve_node_config(SCHEMA, {"nope": 1}, {})
    with pytest.raises(ConfigSchemaError, match="must be >= 1"):
        resolve_node_config(SCHEMA, {}, {"page_size": 0})
    with pytest.raises(ConfigSchemaError, match="no config_schema"):
        resolve_node_config({}, {"page_size": 1}, {})


def test_workspace_node_overrides_are_scoped_by_workflow() -> None:
    workspace = {
        "node_config": {
            "wf": {"generate": {"page_size": 10}},
            "other": {"generate": {"page_size": 99}},
        }
    }
    assert workspace_node_overrides(workspace, "wf") == {"generate": {"page_size": 10}}
    assert workspace_node_overrides(workspace, "missing") == {}
    assert workspace_node_overrides(None, "wf") == {}


def test_resolve_workflow_node_configs_merges_all_layers() -> None:
    definition = _definition({"subject_id": "physics"})
    workspace = {"node_config": {"wf": {"generate": {"page_size": 5}}}}
    resolved = resolve_workflow_node_configs(
        definition, {"a": _agent("generate", SCHEMA)}, workspace
    )
    assert resolved == {"generate": {"page_size": 5, "subject_id": "physics"}}


def test_resolve_workflow_node_configs_skips_plain_nodes() -> None:
    assert resolve_workflow_node_configs(_definition(), {"a": _agent("generate")}, None) == {}


def test_workflow_node_config_schemas_maps_nodes() -> None:
    schemas = workflow_node_config_schemas(_definition(), {"a": _agent("generate", SCHEMA)})
    assert schemas == {"generate": SCHEMA}


def test_frozen_node_config_reads_batch_payload() -> None:
    payload = {"node_config": {"generate": {"page_size": 7}}}
    assert frozen_node_config(payload, "generate") == {"page_size": 7}
    assert frozen_node_config(payload, "missing") is None
    assert frozen_node_config({}, "generate") is None
    assert frozen_node_config(None, "generate") is None


def test_dispatch_effective_config_prefers_frozen_snapshot() -> None:
    node = _definition().nodes["generate"]
    workspace = {"node_config": {"wf": {"generate": {"page_size": 10}}}}
    frozen = {"node_config": {"generate": {"page_size": 7}}}
    assert dispatch_effective_config(SCHEMA, node, "wf", workspace, frozen) == {"page_size": 7}
    assert dispatch_effective_config(SCHEMA, node, "wf", workspace, None) == {"page_size": 10}


EXECUTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "bank_version": {"type": "string", "default": "v5"},
        "country_id": {"type": "string"},
    },
}


def _executors(schema: dict | None = None) -> dict[str, LocalExecutorConfig]:
    return {
        "local-default": LocalExecutorConfig(
            kind="local",
            global_capacity=2,
            capabilities={
                "fetch": LocalCapabilityConfig(handler="mod.fetch", config_schema=schema or {}),
            },
        )
    }


def _fetch_definition(node_config: dict | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="wf",
        label="Wf",
        intake=WorkflowIntake(),
        nodes={
            "fetch": WorkflowNode(
                key="fetch",
                label="Fetch",
                capability="fetch",
                config=node_config or {},
            )
        },
    )


def test_executor_capability_schemas_skip_undeclared_capabilities() -> None:
    assert executor_capability_config_schemas(_executors(EXECUTOR_SCHEMA)) == {
        "fetch": EXECUTOR_SCHEMA
    }
    assert executor_capability_config_schemas(_executors()) == {}


def test_capability_schemas_agent_wins_executor_fallback() -> None:
    agents = {"a": _agent("generate", SCHEMA)}
    schemas = capability_config_schemas(agents, _executors(EXECUTOR_SCHEMA))
    assert schemas == {"generate": SCHEMA, "fetch": EXECUTOR_SCHEMA}
    # Same capability on both sides: the Agent Definition schema wins.
    agents_both = {"a": _agent("fetch", SCHEMA)}
    assert capability_config_schemas(agents_both, _executors(EXECUTOR_SCHEMA)) == {"fetch": SCHEMA}


def test_executor_definition_capability_schema_targets_one_executor() -> None:
    executors = _executors(EXECUTOR_SCHEMA)
    assert executor_definition_capability_schema(executors, "local-default", "fetch") == (
        EXECUTOR_SCHEMA
    )
    assert executor_definition_capability_schema(executors, "local-default", "other") == {}
    assert executor_definition_capability_schema(executors, "missing", "fetch") == {}


def test_workflow_node_config_schemas_includes_executor_nodes() -> None:
    schemas = workflow_node_config_schemas(_fetch_definition(), {}, _executors(EXECUTOR_SCHEMA))
    assert schemas == {"fetch": EXECUTOR_SCHEMA}


def test_resolve_workflow_node_configs_executor_chain() -> None:
    definition = _fetch_definition({"country_id": "1"})
    workspace = {"node_config": {"wf": {"fetch": {"country_id": "9"}}}}
    resolved = resolve_workflow_node_configs(definition, {}, workspace, _executors(EXECUTOR_SCHEMA))
    # defaults → node config → workspace override
    assert resolved == {"fetch": {"bank_version": "v5", "country_id": "9"}}


def test_resolve_workflow_node_configs_executor_rejects_invalid_override() -> None:
    definition = _fetch_definition()
    workspace = {"node_config": {"wf": {"fetch": {"nope": 1}}}}
    with pytest.raises(ConfigSchemaError, match="unknown keys"):
        resolve_workflow_node_configs(definition, {}, workspace, _executors(EXECUTOR_SCHEMA))

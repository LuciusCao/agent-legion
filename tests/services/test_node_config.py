from __future__ import annotations

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.config_schema import ConfigSchemaError
from server.app.services.node_config import (
    capability_config_schemas,
    dispatch_effective_config,
    resolve_node_config,
    resolve_workflow_node_configs,
    workflow_node_config_schemas,
    workspace_node_overrides,
)
from server.app.services.node_config_batch import frozen_node_config
from server.app.services.node_config_runtime import runtime_mutable_keys
from server.app.services.node_execution_config import (
    merge_reserved_execution_schema,
    reserved_execution_defaults,
)
from server.app.workflows.node_config_schema import load_node_config_schema
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    WorkflowIntake,
    WorkflowNode,
)

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


MUTABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "subject_id": {"type": "string", "enum": ["math", "physics"]},
        "dry_run": {"type": "boolean", "default": False, "runtime_mutable": True},
        "max_retries": {"type": "integer", "default": 0, "runtime_mutable": True},
    },
}


def test_runtime_mutable_keys_reads_marker() -> None:
    assert runtime_mutable_keys(MUTABLE_SCHEMA) == frozenset({"dry_run", "max_retries"})
    assert runtime_mutable_keys(SCHEMA) == frozenset()
    assert runtime_mutable_keys({}) == frozenset()
    assert runtime_mutable_keys({"properties": "not-a-mapping"}) == frozenset()


def test_runtime_mutable_keys_ignore_reserved_execution_keys() -> None:
    # Platform-reserved execution keys always stay intake-frozen, even when a
    # hand-built schema marks them (node config_schemas cannot redeclare them
    # at all — the workflow loader rejects that).
    marked = {
        "properties": {
            "timeout_seconds": {"type": "integer", "runtime_mutable": True},
            "sandbox_network": {"type": "boolean", "runtime_mutable": True},
            "dry_run": {"type": "boolean", "runtime_mutable": True},
        }
    }
    assert runtime_mutable_keys(marked) == frozenset({"dry_run"})


def test_runtime_mutable_keys_re_resolve_for_existing_job() -> None:
    # The workspace override changed AFTER intake: the next node execution of
    # the already-intaken job picks it up for marked keys only; unmarked keys
    # keep the frozen intake value.
    node = _definition().nodes["generate"]
    frozen = {
        "node_config": {"generate": {"subject_id": "math", "dry_run": False, "max_retries": 1}}
    }
    workspace = {
        "node_config": {
            "wf": {"generate": {"subject_id": "physics", "dry_run": True, "max_retries": 3}}
        }
    }
    effective = dispatch_effective_config(MUTABLE_SCHEMA, node, "wf", workspace, frozen)
    assert effective == {"subject_id": "math", "dry_run": True, "max_retries": 3}


def test_runtime_mutable_falls_back_when_override_removed() -> None:
    # The post-intake override was removed again: live re-resolution falls
    # back to the node config layer and schema defaults.
    node = _definition({"dry_run": True}).nodes["generate"]
    frozen = {"node_config": {"generate": {"dry_run": False, "max_retries": 1}}}
    effective = dispatch_effective_config(MUTABLE_SCHEMA, node, "wf", None, frozen)
    assert effective == {"dry_run": True, "max_retries": 0}


def test_runtime_mutable_live_re_resolution_is_validated() -> None:
    node = _definition().nodes["generate"]
    frozen = {"node_config": {"generate": {"max_retries": 1}}}
    workspace = {"node_config": {"wf": {"generate": {"max_retries": "many"}}}}
    with pytest.raises(ConfigSchemaError, match="must be of type integer"):
        dispatch_effective_config(MUTABLE_SCHEMA, node, "wf", workspace, frozen)


def test_reserved_execution_keys_stay_frozen_at_dispatch() -> None:
    node = _definition({"timeout_seconds": 30}).nodes["generate"]
    schema = merge_reserved_execution_schema(
        {"properties": {"dry_run": {"type": "boolean", "runtime_mutable": True}}}
    )
    frozen = {"node_config": {"generate": {"timeout_seconds": 30, "dry_run": False}}}
    workspace = {"node_config": {"wf": {"generate": {"timeout_seconds": 5, "dry_run": True}}}}
    effective = dispatch_effective_config(schema, node, "wf", workspace, frozen)
    assert effective["timeout_seconds"] == 30
    assert effective["dry_run"] is True


def test_node_config_schema_rejects_reserved_keys_marked_mutable() -> None:
    raw = {
        "config_schema": {
            "properties": {"timeout_seconds": {"type": "integer", "runtime_mutable": True}}
        }
    }
    with pytest.raises(WorkflowDefinitionError, match="platform-reserved"):
        load_node_config_schema(raw, "intake")


def test_resolve_workflow_node_configs_rejects_invalid_override() -> None:
    definition = _node_schema_definition()
    workspace = {"node_config": {"wf": {"intake": {"nope": 1}}}}
    with pytest.raises(ConfigSchemaError, match="unknown keys"):
        resolve_workflow_node_configs(definition, {}, workspace)


NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "knowledge_dir": {"type": "string", "default": "kb"},
    },
}


def _node_schema_definition(node_config: dict | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="wf",
        label="Wf",
        intake=WorkflowIntake(),
        nodes={
            "intake": WorkflowNode(
                key="intake",
                label="Intake",
                capability="intake",
                config=node_config or {},
                config_schema=NODE_SCHEMA,
            )
        },
    )


def test_capability_schemas_agent_layer_wins_over_node() -> None:
    definition = _node_schema_definition()
    # Agent Definition wins over the node-declared schema (P-0.5: the
    # executor fallback layer is gone).
    agents = {"a": _agent("intake", SCHEMA)}
    assert capability_config_schemas(agents, definition)["intake"] == SCHEMA
    assert capability_config_schemas({}, definition)["intake"] == NODE_SCHEMA


def test_resolve_workflow_node_configs_node_declared_schema() -> None:
    definition = _node_schema_definition({"knowledge_dir": "custom"})
    resolved = resolve_workflow_node_configs(definition, {}, None)
    # Node-declared schema applies without any executor; reserved execution
    # keys merge in with platform defaults (no executor to seed from).
    assert resolved == {
        "intake": {"knowledge_dir": "custom", "timeout_seconds": 600, "sandbox_network": False}
    }


def test_workflow_node_config_schemas_node_declared_schema() -> None:
    schemas = workflow_node_config_schemas(_node_schema_definition(), {})
    properties = schemas["intake"]["properties"]
    assert properties["knowledge_dir"] == {"type": "string", "default": "kb"}
    assert properties["timeout_seconds"]["default"] == 600
    assert properties["sandbox_network"]["default"] is False


def test_resolve_workflow_node_configs_agent_nodes_skip_reserved_keys() -> None:
    # Agent-routed nodes keep their Agent Definition schema untouched.
    resolved = resolve_workflow_node_configs(_definition(), {"a": _agent("generate", SCHEMA)}, None)
    assert resolved == {"generate": {"page_size": 50}}


def test_resolve_workflow_node_configs_reserved_keys_overridable() -> None:
    definition = _node_schema_definition({"timeout_seconds": 30})
    workspace = {"node_config": {"wf": {"intake": {"sandbox_network": True}}}}
    resolved = resolve_workflow_node_configs(definition, {}, workspace)
    assert resolved["intake"]["timeout_seconds"] == 30
    assert resolved["intake"]["sandbox_network"] is True


def test_resolve_workflow_node_configs_reserved_keys_validated() -> None:
    with pytest.raises(ConfigSchemaError, match="must be >= 1"):
        resolve_workflow_node_configs(_node_schema_definition({"timeout_seconds": 0}), {}, None)
    with pytest.raises(ConfigSchemaError, match="must be of type boolean"):
        resolve_workflow_node_configs(_node_schema_definition({"sandbox_network": "yes"}), {}, None)


def test_dispatch_effective_config_pads_frozen_with_fallback_defaults() -> None:
    node = _definition().nodes["generate"]
    defaults = {"timeout_seconds": 900, "sandbox_network": True}
    frozen = {"node_config": {"generate": {"page_size": 7}}}
    # Frozen snapshots predating the reserved keys get padded underneath.
    assert dispatch_effective_config(SCHEMA, node, "wf", None, frozen, defaults) == {
        "page_size": 7,
        "timeout_seconds": 900,
        "sandbox_network": True,
    }
    # Frozen values always win over the padding.
    frozen_new = {"node_config": {"generate": {"timeout_seconds": 30}}}
    padded = dispatch_effective_config(SCHEMA, node, "wf", None, frozen_new, defaults)
    assert padded["timeout_seconds"] == 30
    assert padded["sandbox_network"] is True
    # No padding without the argument (agent path unchanged).
    assert dispatch_effective_config(SCHEMA, node, "wf", None, frozen) == {"page_size": 7}


def test_merge_reserved_execution_schema_declared_wins() -> None:
    # Legacy executor config_schemas may declare timeout_seconds themselves;
    # the declared property keeps precedence over the platform default.
    legacy = {
        "type": "object",
        "properties": {"timeout_seconds": {"type": "integer", "default": 900}},
    }
    merged = merge_reserved_execution_schema(legacy)
    assert merged["properties"]["timeout_seconds"] == {"type": "integer", "default": 900}
    assert merged["properties"]["sandbox_network"] == {"type": "boolean", "default": False}


def test_reserved_execution_defaults_validate_seed() -> None:
    assert reserved_execution_defaults() == {"timeout_seconds": 600, "sandbox_network": False}
    assert reserved_execution_defaults({"timeout_seconds": 0, "sandbox_network": "yes"}) == {
        "timeout_seconds": 600,
        "sandbox_network": False,
    }
    assert reserved_execution_defaults({"timeout_seconds": 30, "sandbox_network": True}) == {
        "timeout_seconds": 30,
        "sandbox_network": True,
    }

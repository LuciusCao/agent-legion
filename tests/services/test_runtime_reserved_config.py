"""#691: ``timeout_seconds`` is runtime-adjustable, ``sandbox_network`` versioned.

Pure resolution tests (no database): the dispatch-time chain
(schema default → node config → workspace override, evaluated at dispatch,
not at intake), its audit (value + source), and the claim-time refresh of
requests still queued for a Worker.
"""

from __future__ import annotations

import json

import pytest

from server.app.agent_broker.agent_claim_compatibility import live_claim_manifest
from server.app.agent_broker.claim_timeout import refresh_claim_timeout
from server.app.config_schema import ConfigSchemaError
from server.app.services.node_config import dispatch_config_resolution
from server.app.services.node_execution_config import (
    AGENT_DEFAULT_TIMEOUT_SECONDS,
    merge_reserved_execution_schema,
    node_config_reserved_defaults,
)
from server.app.services.runtime_reserved_config import (
    CONFIG_RESOLUTION_AUDIT_KEY,
    CONFIG_RESOLUTION_MANIFEST_KEY,
    audit_snapshot,
    claim_time_timeout,
    resolve_runtime_reserved,
)
from server.app.workflows.schema import WorkflowNode

pytestmark = pytest.mark.no_db

CODE_SCHEMA = merge_reserved_execution_schema(
    {"properties": {"mode": {"type": "string", "default": "fast"}}}
)
AGENT_SCHEMA = merge_reserved_execution_schema(
    {}, {"timeout_seconds": AGENT_DEFAULT_TIMEOUT_SECONDS}
)


def _node(config: dict | None = None, node_type: str = "code") -> WorkflowNode:
    return WorkflowNode(
        key="fetch", label="Fetch", capability="fetch", config=config or {}, node_type=node_type
    )


def _workspace(override: dict | None) -> dict:
    return {"node_config": {"wf": {"fetch": override}}} if override is not None else {}


def _frozen(values: dict) -> dict:
    return {"node_config": {"fetch": values}}


# --- dispatch-time resolution ------------------------------------------------


def test_queued_code_job_picks_up_override_changed_after_intake() -> None:
    # Intake froze 600 (no override yet); the operator raised the override
    # afterwards — the not-yet-dispatched node runs with the new value.
    frozen = _frozen({"mode": "fast", "timeout_seconds": 600, "sandbox_network": False})
    config, resolution = dispatch_config_resolution(
        CODE_SCHEMA, _node(), "wf", _workspace({"timeout_seconds": 3600}), frozen
    )
    assert config["timeout_seconds"] == 3600
    assert resolution == {"timeout_seconds": {"value": 3600, "source": "workspace_override"}}


def test_queued_agent_job_picks_up_override_changed_after_intake() -> None:
    node = _node(node_type="agent")
    frozen = _frozen({"timeout_seconds": 1800, "sandbox_network": False})
    config, resolution = dispatch_config_resolution(
        AGENT_SCHEMA,
        node,
        "wf",
        _workspace({"timeout_seconds": 7200}),
        frozen,
        fallback_defaults=node_config_reserved_defaults(node.config),
    )
    assert config["timeout_seconds"] == 7200
    assert resolution["timeout_seconds"]["source"] == "workspace_override"


@pytest.mark.parametrize(
    ("schema", "node_config", "override", "expected"),
    [
        # workspace override > node config > platform default
        (
            CODE_SCHEMA,
            {"timeout_seconds": 900},
            {"timeout_seconds": 1200},
            (1200, "workspace_override"),
        ),
        (CODE_SCHEMA, {"timeout_seconds": 900}, {}, (900, "node_config")),
        (CODE_SCHEMA, {}, {}, (600, "platform_default")),
        (
            AGENT_SCHEMA,
            {"timeout_seconds": 900},
            {"timeout_seconds": 1200},
            (1200, "workspace_override"),
        ),
        (AGENT_SCHEMA, {"timeout_seconds": 900}, {}, (900, "node_config")),
        (AGENT_SCHEMA, {}, {}, (1800, "platform_default")),
    ],
)
def test_precedence_is_evaluated_at_dispatch(schema, node_config, override, expected) -> None:
    # The frozen value (from a different, older chain state) never wins.
    frozen = _frozen({"timeout_seconds": 42, "sandbox_network": False})
    config, resolution = dispatch_config_resolution(
        schema, _node(node_config), "wf", _workspace(override), frozen
    )
    assert (config["timeout_seconds"], resolution["timeout_seconds"]["source"]) == expected
    assert resolution["timeout_seconds"]["value"] == expected[0]


def test_removed_override_falls_back_to_node_config_at_dispatch() -> None:
    # Intake froze the then-present override; it was removed afterwards.
    frozen = _frozen({"timeout_seconds": 3600, "sandbox_network": False})
    config, resolution = dispatch_config_resolution(
        CODE_SCHEMA, _node({"timeout_seconds": 900}), "wf", _workspace(None), frozen
    )
    assert config["timeout_seconds"] == 900
    assert resolution["timeout_seconds"]["source"] == "node_config"


def test_sandbox_network_stays_intake_frozen() -> None:
    # Regression guard: network egress is a security boundary — a live
    # override never opens the network for an already-intaken job.
    frozen = _frozen({"timeout_seconds": 600, "sandbox_network": False})
    config, resolution = dispatch_config_resolution(
        CODE_SCHEMA,
        _node(),
        "wf",
        _workspace({"sandbox_network": True, "timeout_seconds": 60}),
        frozen,
    )
    assert config["sandbox_network"] is False
    assert config["timeout_seconds"] == 60
    assert set(resolution) == {"timeout_seconds"}


def test_unfrozen_dispatch_reports_resolution_too() -> None:
    config, resolution = dispatch_config_resolution(
        CODE_SCHEMA, _node(), "wf", _workspace({"sandbox_network": True}), None
    )
    assert config["sandbox_network"] is True  # live chain when nothing is frozen
    assert resolution == {"timeout_seconds": {"value": 600, "source": "platform_default"}}


def test_invalid_live_timeout_override_fails_the_dispatch() -> None:
    frozen = _frozen({"timeout_seconds": 600})
    with pytest.raises(ConfigSchemaError, match=r"workspace node config\.timeout_seconds"):
        dispatch_config_resolution(
            CODE_SCHEMA, _node(), "wf", _workspace({"timeout_seconds": 0}), frozen
        )


def test_resolution_skips_schemas_without_the_reserved_key() -> None:
    assert resolve_runtime_reserved({}, {"timeout_seconds": 5}, {}) == {}


def test_audit_snapshot_adds_resolution_meta_key() -> None:
    resolution = {"timeout_seconds": {"value": 5, "source": "node_config"}}
    assert audit_snapshot({"mode": "fast"}, resolution) == {
        "mode": "fast",
        CONFIG_RESOLUTION_AUDIT_KEY: resolution,
    }
    assert audit_snapshot({"mode": "fast"}, None) == {"mode": "fast"}


def test_claim_time_timeout_skips_malformed_layers() -> None:
    assert claim_time_timeout(1800, {"timeout_seconds": 900}, {"timeout_seconds": "x"}) == {
        "value": 900,
        "source": "node_config",
    }
    assert claim_time_timeout(600, {"timeout_seconds": True}, {}) == {
        "value": 600,
        "source": "platform_default",
    }


# --- claim-time refresh (requests queued for a Worker) -----------------------


def _row(revision_nodes: dict | None, workspace_config: dict | None, kind: str) -> dict:
    row = {
        "node_key": "fetch",
        "workspace_id": "wf",
        "kind": kind,
        "revision_definition_json": (
            json.dumps({"nodes": revision_nodes}) if revision_nodes is not None else None
        ),
    }
    if workspace_config is not None:
        row["workspace_node_config_json"] = json.dumps(workspace_config)
    return row


def test_claim_refresh_updates_queued_code_manifest() -> None:
    manifest = {
        "workflow_key": "wf",
        "timeout_seconds": 600,
        "config": {"mode": "fast", "timeout_seconds": 600},
        CONFIG_RESOLUTION_MANIFEST_KEY: {
            "timeout_seconds": {"value": 600, "source": "platform_default"}
        },
    }
    row = _row(
        {"fetch": {"config": {"timeout_seconds": 900}}},
        {"wf": {"fetch": {"timeout_seconds": 2400}}},
        "code",
    )
    refresh_claim_timeout(manifest, row, "code")
    assert manifest["timeout_seconds"] == 2400
    assert manifest["config"]["timeout_seconds"] == 2400
    assert manifest[CONFIG_RESOLUTION_MANIFEST_KEY]["timeout_seconds"] == {
        "value": 2400,
        "source": "workspace_override",
    }


def test_claim_refresh_keeps_enqueue_value_without_scan_inputs() -> None:
    manifest = {"timeout_seconds": 600}
    # No workspace column (e.g. the unclaimable sweeper's query).
    refresh_claim_timeout(manifest, _row({"fetch": {}}, None, "code"), "code")
    # No pinned revision (legacy job): no node layer to re-read.
    refresh_claim_timeout(
        manifest, _row(None, {"wf": {"fetch": {"timeout_seconds": 5}}}, "code"), "code"
    )
    assert manifest == {"timeout_seconds": 600}


def test_live_claim_manifest_refreshes_agent_timeout_and_command_spec() -> None:
    manifest = {
        "runtime": "velites",
        "workflow_key": "wf",
        "execution": {
            "binary": "velites",
            "provider": "gw",
            "model": "m",
            "thinking": "",
            "timeout_seconds": 1800,
            "no_sandbox": False,
        },
        "job_id": "job-1",
        "node_key": "fetch",
        "tools": [],
        "inputs": [],
        "expected_outputs": ["output.json"],
    }
    row = {
        "manifest_json": json.dumps(manifest),
        **_row(
            {"fetch": {"execution": {"provider": "gw", "model": "m"}, "config": {}}},
            {"wf": {"fetch": {"timeout_seconds": 5400}}},
            "agent",
        ),
    }
    resolved = live_claim_manifest(row)
    assert resolved["execution"]["timeout_seconds"] == 5400
    command = resolved["command_spec"]["command"]
    assert command[command.index("--timeout-seconds") + 1] == "5400"
    assert resolved[CONFIG_RESOLUTION_MANIFEST_KEY]["timeout_seconds"] == {
        "value": 5400,
        "source": "workspace_override",
    }
    # Override removed again before the claim: back to the agent platform default.
    row["workspace_node_config_json"] = "{}"
    assert live_claim_manifest(row)["execution"]["timeout_seconds"] == 1800

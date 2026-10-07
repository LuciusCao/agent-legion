"""Claim-time check of the quality-replay node profile pin (#1079, #440 D6).

The replay setup transplants the chosen profile into the copy job's
snapshot; the claim only re-verifies that the snapshot node still matches
the frozen ``node_profiles`` pin and fails the node closed otherwise.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from server.app.services.agent_node_profile_types import (
    PROFILE_SOURCE_DEFINITION,
    PROFILE_SOURCE_NODE,
    AgentNodeProfile,
)
from server.app.services.node_profile_pins import node_profile_hash, node_profile_pin
from server.app.workflow_worker.agent_claim_profile import resolve_claim_profile
from server.app.workflows.schema import WorkflowNode, WorkflowNodeExecution

pytestmark = pytest.mark.no_db

_NODE = WorkflowNode(
    key="generate",
    label="generate",
    capability="write_script",
    node_type="agent",
    execution=WorkflowNodeExecution(runtime="pi", provider="p", model="m"),
)


def _pin(node: WorkflowNode) -> dict[str, Any]:
    return {"revision_id": "ws:v2", "node_key": node.key, "profile_hash": node_profile_hash(node)}


def _claim(node: WorkflowNode, pin: dict[str, Any] | None, source: str = PROFILE_SOURCE_NODE):
    # The node source never touches the worker (no catalog read).
    return resolve_claim_profile(cast(Any, None), "ws", node.key, node, None, source, pin)


def test_matching_pin_dispatches_the_snapshot_profile() -> None:
    profile = _claim(_NODE, _pin(_NODE))
    assert isinstance(profile, AgentNodeProfile)
    assert profile.runtime == "pi"


def test_profile_drift_fails_closed() -> None:
    drifted = replace(_NODE, execution=replace(_NODE.execution, model="other"))
    message = _claim(drifted, _pin(_NODE))
    assert isinstance(message, str)
    assert "does not match its replay pin" in message


def test_pin_for_another_node_fails_closed() -> None:
    message = _claim(_NODE, {**_pin(_NODE), "node_key": "elsewhere"})
    assert isinstance(message, str) and "targets 'elsewhere'" in message


def test_pin_on_a_definition_sourced_node_fails_closed() -> None:
    message = _claim(_NODE, _pin(_NODE), PROFILE_SOURCE_DEFINITION)
    assert isinstance(message, str) and "not a self-contained agent node" in message


def test_pin_reader_tolerates_missing_or_malformed_payloads() -> None:
    assert node_profile_pin(None, "generate") is None
    assert node_profile_pin({"node_profiles": []}, "generate") is None
    assert node_profile_pin({"node_profiles": {"generate": _pin(_NODE)}}, "generate") == _pin(_NODE)

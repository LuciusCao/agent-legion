from __future__ import annotations

import json
from dataclasses import replace

import pytest

from server.app.executors.config import RemoteCapabilityConfig, load_executor_definitions
from server.app.executors.kinds import RuntimeDependencies
from server.app.executors.models import ExecutionContext
from server.app.executors.remote_payloads import get_payload_builder
from server.app.executors.remote_payloads.pi import PiPayloadBuilder
from server.app.executors.runtime_config import PiRuntimeConfig
from server.app.skills.manager import SkillManager


def test_pi_builder_registered() -> None:
    assert get_payload_builder("pi")(RuntimeDependencies(), {}).name == "pi"


def test_unknown_builder_raises() -> None:
    with pytest.raises(KeyError, match="nope"):
        get_payload_builder("nope")


def test_build_manifest_golden(
    skill_manager: SkillManager, execution_context: ExecutionContext
) -> None:
    builder = PiPayloadBuilder(
        PiRuntimeConfig(),
        skill_manager,
        {"cap": RemoteCapabilityConfig(skill="video_knowledge/gen")},
    )
    manifest = builder.build_manifest(execution_context)
    assert manifest["job_id"] == execution_context.job_id
    assert manifest["node_key"] == execution_context.node_key
    assert manifest["inputs"] == list(execution_context.inputs)
    assert manifest["expected_outputs"] == list(execution_context.expected_outputs)
    assert manifest["tools"] == ["read", "write", "bash"]
    assert manifest["skill"] == "video_knowledge/gen"
    assert set(manifest["pi"]) == {
        "binary",
        "provider",
        "model",
        "thinking",
        "timeout_seconds",
        "environment",
    }
    assert len(manifest["run_token"]) == 12


def test_command_spec_contains_no_environment(
    skill_manager: SkillManager, execution_context: ExecutionContext
) -> None:
    builder = PiPayloadBuilder(
        PiRuntimeConfig(environment={"SECRET": "x"}),
        skill_manager,
        {"cap": RemoteCapabilityConfig(skill="video_knowledge/gen")},
    )
    spec = builder.build_command_spec(builder.build_manifest(execution_context))
    assert "SECRET" not in json.dumps(spec)


def test_manifest_carries_shard_fields(
    skill_manager: SkillManager, execution_context: ExecutionContext
) -> None:
    builder = PiPayloadBuilder(
        PiRuntimeConfig(),
        skill_manager,
        {"cap": RemoteCapabilityConfig(skill="video_knowledge/gen")},
    )
    context = replace(
        execution_context,
        runtime={"shard_index": 1, "shard_input": {"q": 2}},
    )
    manifest = builder.build_manifest(context)
    assert manifest["shard_index"] == 1
    assert manifest["shard_input"] == {"q": 2}


def test_remote_config_payload_field_defaults_pi() -> None:
    defs = load_executor_definitions(
        {
            "r": {
                "kind": "remote",
                "global_capacity": 1,
                "capabilities": {"c": {"skill": "a/b"}},
            }
        }
    )
    assert defs["r"].payload == "pi"


def test_remote_config_rejects_unknown_payload() -> None:
    with pytest.raises(Exception, match="payload"):
        load_executor_definitions(
            {
                "r": {
                    "kind": "remote",
                    "payload": "nope",
                    "global_capacity": 1,
                    "capabilities": {"c": {"skill": "a/b"}},
                }
            }
        )

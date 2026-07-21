from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.executors.config import (
    RemoteCapabilityConfig,
    RemoteExecutorConfig,
    load_executor_definitions,
)
from server.app.executors.kinds import RuntimeDependencies
from server.app.executors.models import ExecutionContext
from server.app.executors.remote import build_remote_executor
from server.app.executors.remote_broker import RemoteClaim, RemoteExecutionBroker, RemoteOutcome
from server.app.executors.remote_payloads import get_payload_builder
from server.app.executors.remote_payloads.openclaw import OpenClawPayloadBuilder
from server.app.executors.runtime_config import OpenClawRuntimeConfig


def _builder() -> OpenClawPayloadBuilder:
    runtime = OpenClawRuntimeConfig(
        command_template=("openclaw", "--agent", "{agent_id}", "run"),
        timeout_seconds=120,
    )
    return OpenClawPayloadBuilder(
        runtime,
        agent_id="hive-agent",
        capabilities={"cap": RemoteCapabilityConfig(skill="a/b")},
    )


def test_openclaw_builder_registered() -> None:
    factory = get_payload_builder("openclaw")
    assert factory is not None


def test_build_manifest_contains_openclaw_section(execution_context) -> None:
    manifest = _builder().build_manifest(execution_context)
    assert manifest["capability"] == execution_context.capability
    oc = manifest["openclaw"]
    assert oc["command_template"][0] == "openclaw"
    assert "hive-agent" in oc["command_template"]
    assert "{agent_id}" not in oc["command_template"]
    assert oc["timeout_seconds"] == 120
    assert manifest["expected_outputs"] == list(execution_context.expected_outputs)


def test_build_manifest_carries_remote_executor_contract_keys(execution_context) -> None:
    # RemoteExecutor.execute consumes run_token/skill_version from the manifest.
    manifest = _builder().build_manifest(execution_context)
    assert manifest["run_token"]
    assert "skill_version" in manifest


def test_build_manifest_carries_shard_fields(execution_context) -> None:
    context = replace(
        execution_context,
        runtime={"shard_index": 1, "shard_input": {"q": 2}},
    )
    manifest = _builder().build_manifest(context)
    assert manifest["shard_index"] == 1
    assert manifest["shard_input"] == {"q": 2}


def test_command_spec_prompt_mentions_skill_and_outputs(execution_context) -> None:
    builder = _builder()
    spec = builder.build_command_spec(builder.build_manifest(execution_context))
    assert "a/b" in spec["prompt"]
    for name in execution_context.expected_outputs:
        assert name in spec["prompt"]
    assert set(spec) == {"version", "prompt", "command"}


def test_command_spec_appends_prompt_file_placeholder(execution_context) -> None:
    builder = _builder()
    spec = builder.build_command_spec(builder.build_manifest(execution_context))
    assert spec["command"] == ["openclaw", "--agent", "hive-agent", "run", "{prompt_file}"]


def test_command_spec_keeps_template_prompt_placeholder(execution_context) -> None:
    runtime = OpenClawRuntimeConfig(
        command_template=(
            "openclaw",
            "--agent",
            "{agent_id}",
            "run",
            "--message",
            "{prompt_text}",
        ),
        timeout_seconds=120,
    )
    builder = OpenClawPayloadBuilder(
        runtime,
        agent_id="hive-agent",
        capabilities={"cap": RemoteCapabilityConfig(skill="a/b")},
    )
    spec = builder.build_command_spec(builder.build_manifest(execution_context))
    assert spec["command"] == [
        "openclaw",
        "--agent",
        "hive-agent",
        "run",
        "--message",
        "{prompt_text}",
    ]


def test_scan_error_is_none(tmp_path) -> None:
    assert _builder().scan_error(tmp_path / "whatever.jsonl") is None


def test_remote_config_openclaw_payload_requires_agent_id() -> None:
    with pytest.raises(Exception, match="agent_id"):
        load_executor_definitions(
            {
                "r": {
                    "kind": "remote",
                    "payload": "openclaw",
                    "global_capacity": 1,
                    "capabilities": {"c": {"skill": "a/b"}},
                }
            }
        )


def test_remote_config_openclaw_payload_ok() -> None:
    defs = load_executor_definitions(
        {
            "r": {
                "kind": "remote",
                "payload": "openclaw",
                "agent_id": "hive-agent",
                "global_capacity": 1,
                "capabilities": {"c": {"skill": "a/b"}},
            }
        }
    )
    assert defs["r"].payload == "openclaw"
    assert defs["r"].agent_id == "hive-agent"


def test_remote_executor_openclaw_submit_and_dequeue(
    tmp_path: Path, execution_context: ExecutionContext
) -> None:
    # Integration: remote executor with openclaw payload, submit -> fake worker
    # dequeue. The executor is submit-only (Task 6): execute returns None once
    # the row is enqueued; the completion callback path is covered separately.
    for rel in execution_context.inputs:
        (execution_context.job_dir / rel).write_text("{}", encoding="utf-8")
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")

    class StubArtifactStore:
        def put(self, data: bytes) -> str:
            return "ab" * 32

        def add_ref(self, job_id: str, node_key: str, name: str, digest: str) -> None:
            return None

    config = RemoteExecutorConfig(
        kind="remote",
        payload="openclaw",
        agent_id="hive-agent",
        global_capacity=1,
        capabilities={"cap": RemoteCapabilityConfig(skill="a/b")},
    )
    deps = RuntimeDependencies(
        openclaw_runtime=OpenClawRuntimeConfig(
            command_template=("openclaw", "--agent", "{agent_id}", "run"),
            timeout_seconds=120,
        ),
        remote_broker=broker,
        artifact_store=StubArtifactStore(),  # type: ignore[arg-type]
    )
    executor = build_remote_executor("oc-remote", config, deps)

    claimed: list[RemoteClaim] = []

    def fake_worker() -> None:
        deadline = time.monotonic() + 10
        claim = None
        while time.monotonic() < deadline:
            claim = broker.dequeue("w1", {"cap"})
            if claim is not None:
                break
            time.sleep(0.05)
        assert claim is not None
        claimed.append(claim)
        broker.complete(
            claim.execution_id,
            "w1",
            RemoteOutcome(status="failed", exit_code=1, error_message="stub worker done"),
        )

    worker = threading.Thread(target=fake_worker)
    worker.start()
    assert executor.execute(execution_context) is None
    worker.join(timeout=10)

    assert len(claimed) == 1
    manifest = claimed[0].manifest
    oc = manifest["openclaw"]
    assert "hive-agent" in oc["command_template"]
    assert "{agent_id}" not in oc["command_template"]
    # The command_spec rendered from the claim manifest must be
    # placeholder-substitutable by the worker.
    spec = _builder().build_command_spec(manifest)
    substituted = [
        part.replace("{prompt_file}", "/worker/prompt.md").replace("{job_dir}", "/worker/job")
        for part in spec["command"]
    ]
    assert all("{" not in part for part in substituted)
    assert "{job_dir}" not in spec["prompt"].replace("{job_dir}", "/worker/job")

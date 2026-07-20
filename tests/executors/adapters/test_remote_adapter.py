from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.executors.config import RemoteCapabilityConfig
from server.app.executors.models import ExecutionContext
from server.app.executors.remote import RemoteExecutor
from server.app.executors.remote_broker import RemoteExecutionBroker
from server.app.executors.remote_payloads.pi import PiPayloadBuilder
from server.app.executors.runtime_config import PiRuntimeConfig
from tests.executors.adapters.helpers import _make_skill_manager


@pytest.fixture(autouse=True)
def _declared_inputs(context: ExecutionContext) -> None:
    # The remote bundle packs declared inputs from the job dir; materialize them.
    for rel in context.inputs:
        (context.job_dir / rel).write_text("{}", encoding="utf-8")


def _make_broker(tmp_path: Path, **kwargs: float | int) -> RemoteExecutionBroker:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    return RemoteExecutionBroker(db_path, tmp_path / "bundles", **kwargs)


def _make_executor(tmp_path: Path, broker: RemoteExecutionBroker) -> RemoteExecutor:
    skill_manager = _make_skill_manager(
        tmp_path,
        "question_comprehension_info/generate_key_info",
        validate_script="#!/usr/bin/env python3\n",
    )
    capabilities = {
        "review_keywords": RemoteCapabilityConfig(
            skill="question_comprehension_info/generate_key_info"
        )
    }
    payload_builder = PiPayloadBuilder(
        PiRuntimeConfig(binary="pi", provider="deepseek", model="your-model-b"),
        skill_manager,
        capabilities,
    )
    return RemoteExecutor("pi-remote", payload_builder, capabilities, broker)


def test_execute_submits_and_returns_none(tmp_path: Path, context: ExecutionContext) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)

    assert executor.execute(context) is None

    payload = broker.payload_for(context.execution_id)
    assert payload is not None
    assert payload.lease_id == context.lease_id
    assert payload.job_id == context.job_id
    assert payload.node_key == context.node_key
    assert payload.capability == context.capability
    assert payload.manifest["run_token"]
    assert payload.manifest["skill_version"]
    # execute renders the command spec at submit time; placeholders stay for
    # the worker to substitute with its local paths.
    spec = payload.command_spec
    assert spec is not None and spec["version"] == 1
    assert "{job_dir}" in spec["prompt"]
    assert any("{prompt_file}" in part for part in spec["command"])
    assert (broker.bundle_dir / payload.bundle_name).is_file()
    # The submitted work is claimable by a worker.
    claim = broker.dequeue("w1", {"review_keywords"})
    assert claim is not None and claim.execution_id == context.execution_id
    assert claim.command_spec == spec
    assert broker.active_lease_ids() == [context.lease_id]


def test_unsupported_capability(tmp_path: Path, context: ExecutionContext) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)
    unsupported = dataclasses.replace(context, capability="nope")
    result = executor.execute(unsupported)
    assert result is not None
    assert result.status == "failed"
    assert "not supported" in result.error_message


def test_cancel_before_execute_returns_early_result(
    tmp_path: Path, context: ExecutionContext
) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)
    executor.cancel(context.execution_id)
    result = executor.execute(context)
    assert result is not None
    assert result.status == "cancelled"
    assert broker.payload_for(context.execution_id) is None  # nothing was submitted


def test_cancel_landing_during_submit_cancels_execution(
    tmp_path: Path, context: ExecutionContext
) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)
    original_submit = broker.submit

    def submit_then_cancel(payload: object) -> None:
        original_submit(payload)  # type: ignore[arg-type]
        # A cancel racing the submit must not be lost once the row exists.
        executor.cancel(context.execution_id)

    broker.submit = submit_then_cancel  # type: ignore[method-assign]
    assert executor.execute(context) is None
    assert broker.wait_result(context.execution_id).status == "cancelled"


def test_execute_returns_failed_when_dispatch_raises(
    tmp_path: Path, context: ExecutionContext
) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)
    # Remove a declared input so the bundle build fails before submission.
    (context.job_dir / context.inputs[0]).unlink()
    result = executor.execute(context)
    assert result is not None
    assert result.status == "failed"
    assert "remote dispatch error" in result.error_message
    assert broker.payload_for(context.execution_id) is None

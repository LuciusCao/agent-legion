from __future__ import annotations

import dataclasses
import io
import tarfile
import threading
import time
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.executors.config import RemoteCapabilityConfig
from server.app.executors.models import ExecutionContext
from server.app.executors.remote import RemoteExecutor
from server.app.executors.remote_broker import (
    RemoteExecutionBroker,
    RemoteExecutionPayload,
    RemoteOutcome,
)
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
    return RemoteExecutor(
        "pi-remote",
        PiRuntimeConfig(binary="pi", provider="deepseek", model="your-model-b"),
        skill_manager,
        {
            "review_keywords": RemoteCapabilityConfig(
                skill="question_comprehension_info/generate_key_info"
            )
        },
        broker,
    )


def _result_archive(path: Path, *, node_key: str, run_token: str, output_name: str) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, content in (
            (output_name, b"{}"),
            (f"runs/{node_key}/{run_token}/events.jsonl", b'{"type":"done"}\n'),
            (f"runs/{node_key}/{run_token}/run.json", b"{}"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


def _fake_worker(broker: RemoteExecutionBroker, bundle_dir: Path, output_name: str) -> None:
    # Poll for a claim, then report a successful result archive.
    deadline = time.monotonic() + 10
    claim = None
    while time.monotonic() < deadline:
        claim = broker.dequeue("w1", {"review_keywords"})
        if claim is not None:
            break
        time.sleep(0.05)
    assert claim is not None
    archive = bundle_dir / f"{claim.execution_id}.result.tar.gz"
    _result_archive(
        archive,
        node_key=claim.manifest["node_key"],
        run_token=claim.manifest["run_token"],
        output_name=output_name,
    )
    outcome = RemoteOutcome(
        status="completed",
        exit_code=0,
        command=("pi", "--mode", "json"),
        skill_version=claim.manifest["skill_version"],
        result_archive_name=archive.name,
    )
    assert broker.complete(claim.execution_id, "w1", outcome) is True


def test_execute_happy_path(tmp_path: Path, context: ExecutionContext) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)
    expected_output = context.expected_outputs[0]
    worker = threading.Thread(
        target=_fake_worker, args=(broker, broker.bundle_dir, expected_output)
    )
    worker.start()

    result = executor.execute(context)

    worker.join(timeout=10)
    assert result.status == "completed", result.error_message
    assert result.exit_code == 0
    assert result.command == ("pi", "--mode", "json")
    assert result.produced_artifacts == (expected_output,)
    assert (context.job_dir / expected_output).is_file()
    run_dir = Path(result.run_dir)
    assert run_dir.is_dir()
    assert (run_dir / "events.jsonl").is_file()
    assert result.skill_version  # 40-char git commit from the fake skill repo
    # bundle + result archive cleaned up
    assert list(broker.bundle_dir.iterdir()) == []


def test_execute_returns_failed_when_worker_reports_failure(
    tmp_path: Path, context: ExecutionContext
) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)

    def failing_worker() -> None:
        deadline = time.monotonic() + 10
        claim = None
        while time.monotonic() < deadline:
            claim = broker.dequeue("w1", {"review_keywords"})
            if claim is not None:
                break
            time.sleep(0.05)
        assert claim is not None
        broker.complete(
            claim.execution_id,
            "w1",
            RemoteOutcome(
                status="failed",
                exit_code=1,
                error_message="Missing outputs after Pi run: out.json",
            ),
        )

    worker = threading.Thread(target=failing_worker)
    worker.start()
    result = executor.execute(context)
    worker.join(timeout=10)
    assert result.status == "failed"
    assert "Missing outputs" in result.error_message


def test_cancel_unblocks_execute(tmp_path: Path, context: ExecutionContext) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)
    submitted = threading.Event()
    original_submit = broker.submit

    def submit_then_signal(payload: RemoteExecutionPayload) -> None:
        original_submit(payload)
        submitted.set()

    broker.submit = submit_then_signal

    def canceller() -> None:
        # Cancel only after the execution is enqueued so the signal cannot be lost.
        assert submitted.wait(timeout=10)
        executor.cancel(context.execution_id)

    thread = threading.Thread(target=canceller)
    thread.start()
    result = executor.execute(context)
    thread.join(timeout=10)
    assert result.status == "cancelled"


def test_execute_failed_after_requeue_limit(tmp_path: Path, context: ExecutionContext) -> None:
    broker = _make_broker(tmp_path, claim_timeout_seconds=0.05, requeue_limit=1)
    executor = _make_executor(tmp_path, broker)

    def zombie_worker() -> None:
        # Claim twice without ever heartbeating; broker sweeps and fails.
        for _ in range(2):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                claim = broker.dequeue("w-zombie", {"review_keywords"})
                if claim is not None:
                    break
                time.sleep(0.05)
            time.sleep(0.1)  # let the claim go stale

    worker = threading.Thread(target=zombie_worker)
    worker.start()
    result = executor.execute(context)
    worker.join(timeout=10)
    assert result.status == "failed"
    assert "requeue limit" in result.error_message


def test_unsupported_capability(tmp_path: Path, context: ExecutionContext) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)
    unsupported = dataclasses.replace(context, capability="nope")
    result = executor.execute(unsupported)
    assert result.status == "failed"
    assert "not supported" in result.error_message


def test_execute_fails_when_result_archive_misses_expected_output(
    tmp_path: Path, context: ExecutionContext
) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)
    # Worker reports success but the archive does not contain the expected output.
    worker = threading.Thread(
        target=_fake_worker, args=(broker, broker.bundle_dir, "unexpected.json")
    )
    worker.start()

    result = executor.execute(context)

    worker.join(timeout=10)
    assert result.status == "failed"
    assert "Missing outputs" in result.error_message


def test_execute_fails_when_result_archive_is_not_unpackable(
    tmp_path: Path, context: ExecutionContext
) -> None:
    broker = _make_broker(tmp_path)
    executor = _make_executor(tmp_path, broker)

    def garbage_worker() -> None:
        deadline = time.monotonic() + 10
        claim = None
        while time.monotonic() < deadline:
            claim = broker.dequeue("w1", {"review_keywords"})
            if claim is not None:
                break
            time.sleep(0.05)
        assert claim is not None
        archive = broker.bundle_dir / f"{claim.execution_id}.result.tar.gz"
        archive.write_bytes(b"not a valid tar.gz")
        outcome = RemoteOutcome(
            status="completed",
            exit_code=0,
            command=("pi", "--mode", "json"),
            skill_version=claim.manifest["skill_version"],
            result_archive_name=archive.name,
        )
        assert broker.complete(claim.execution_id, "w1", outcome) is True

    worker = threading.Thread(target=garbage_worker)
    worker.start()

    result = executor.execute(context)

    worker.join(timeout=10)
    assert result.status == "failed"
    assert "failed to unpack" in result.error_message

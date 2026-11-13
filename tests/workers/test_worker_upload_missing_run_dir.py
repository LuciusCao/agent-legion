"""#1350：成功 run 的留痕目录消失不得覆盖已确定的执行结论。"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
from pathlib import Path
from typing import Any

import pytest

from tests.workers.upload_queue_testlib import QueueFakeClient, _execution_dir, _queue, _task
from worker import state_evidence
from worker.host.transfer import HostRequestError

pytestmark = pytest.mark.no_db


@pytest.fixture
def evidence_root(tmp_path: Path) -> Path:
    root = state_evidence.configure_evidence_root(tmp_path / "state")
    try:
        yield root
    finally:
        state_evidence.reset_evidence_root()


class _ArchiveClient(QueueFakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.archives: list[bytes] = []

    def report(self, execution_id: str, lease_id: str, archive: Path) -> tuple[int, bytes]:
        self.archives.append(archive.read_bytes())
        return super().report(execution_id, lease_id, archive)


def _successful_events() -> list[dict[str, Any]]:
    return [
        {"type": "agent_start"},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop"}},
        {"type": "outputs_validation", "missing": [], "violations": []},
        {"type": "agent_end"},
    ]


def _write_emergency(evidence_root: Path, events: list[dict[str, Any]]) -> Path:
    incident = evidence_root / "exec-1__node_a"
    incident.mkdir(parents=True, exist_ok=True)
    dump = incident / state_evidence.EMERGENCY_EVENTS_FILENAME
    dump.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return dump


def _archive_events(client: _ArchiveClient) -> list[dict[str, Any]]:
    with tarfile.open(fileobj=io.BytesIO(client.archives[0])) as tar:
        member = tar.extractfile("runs/node_a/worker/events.jsonl")
        assert member is not None
        return [json.loads(line) for line in member.read().decode("utf-8").splitlines()]


def test_cleaned_run_recovers_from_complete_emergency_events(
    tmp_path: Path, evidence_root: Path
) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _write_emergency(evidence_root, _successful_events())
    shutil.rmtree(work_root / "exec-1" / "job" / "runs" / "node_a" / "worker")
    client = _ArchiveClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["exit_code"] == 0
    assert report["error_message"] == ""
    assert set(report["output_artifacts"]) == {"output.json"}
    assert [event["type"] for event in _archive_events(client)] == [
        "agent_start",
        "message_end",
        "outputs_validation",
        "agent_end",
    ]


def test_recovered_emergency_events_use_outbound_redaction(
    tmp_path: Path, evidence_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-live-supersecretgatewaytoken123"
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", secret)
    events = _successful_events()
    events.insert(
        1,
        {
            "type": "tool_execution_end",
            "result": {"content": [{"type": "text", "text": f"TOKEN={secret}"}]},
        },
    )
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _write_emergency(evidence_root, events)
    shutil.rmtree(work_root / "exec-1" / "job" / "runs" / "node_a" / "worker")
    client = _ArchiveClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    archived = _archive_events(client)
    assert client.reports[0]["status"] == "completed"
    assert archived[1]["result"]["content"][0]["text"] == "TOKEN=***"
    assert secret not in str(archived)


@pytest.mark.parametrize(
    "events",
    [
        [_successful_events()[1], _successful_events()[2], _successful_events()[3]],
        [_successful_events()[0], _successful_events()[3]],
        [
            _successful_events()[0],
            {"type": "outputs_validation", "missing": ["output.json"], "violations": []},
            _successful_events()[3],
        ],
        [
            _successful_events()[0],
            _successful_events()[2],
            {"type": "agent_end", "reason": "cancelled"},
        ],
        _successful_events()[:-1],
    ],
    ids=["no-start", "no-validation", "missing-output-event", "abnormal-end", "no-end"],
)
def test_cleaned_run_with_incomplete_success_evidence_still_fails(
    tmp_path: Path, evidence_root: Path, events: list[dict[str, Any]]
) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _write_emergency(evidence_root, events)
    shutil.rmtree(work_root / "exec-1" / "job" / "runs" / "node_a" / "worker")
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"].startswith("[work-dir-missing]")
    assert report["output_artifacts"] == {}
    assert client.uploads == {}


def test_cleaned_run_with_missing_declared_output_still_fails(
    tmp_path: Path, evidence_root: Path
) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _write_emergency(evidence_root, _successful_events())
    shutil.rmtree(work_root / "exec-1" / "job" / "runs" / "node_a" / "worker")
    (work_root / "exec-1" / "job" / "output.json").unlink()
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    assert client.reports[0]["status"] == "failed"
    assert client.reports[0]["output_artifacts"] == {}
    assert client.uploads == {}


def test_cleanup_before_primary_scan_does_not_count_as_scanned(
    tmp_path: Path, evidence_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from worker.upload import prepare as upload_prepare

    work_root = tmp_path / "work"
    _execution_dir(work_root)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    original_scan = upload_prepare.scan_and_compress_pi_events

    def delete_before_scan(*args: Any, **kwargs: Any):
        shutil.rmtree(run_dir)
        _write_emergency(evidence_root, _successful_events())
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(upload_prepare, "scan_and_compress_pi_events", delete_before_scan)
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"].startswith("[work-dir-missing]")
    assert client.uploads == {}


@pytest.mark.parametrize(
    ("exit_code", "status", "error"),
    [(0, "completed", ""), (7, "failed", "Agent process exited 7")],
)
def test_cleanup_between_scan_and_archive_preserves_scanned_verdict(
    tmp_path: Path,
    evidence_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    status: str,
    error: str,
) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in _successful_events()), encoding="utf-8"
    )
    original_add = tarfile.TarFile.add
    deleted = False

    def delete_before_run_add(self: tarfile.TarFile, name: Any, *args: Any, **kwargs: Any):
        nonlocal deleted
        if Path(name) == run_dir and not deleted:
            deleted = True
            shutil.rmtree(run_dir)
            _write_emergency(evidence_root, [{"type": "agent_end"}])
        return original_add(self, name, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "add", delete_before_run_add)
    client = _ArchiveClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=exit_code))
    queue.shutdown()

    assert deleted
    report = client.reports[0]
    assert report["status"] == status
    assert report["exit_code"] == exit_code
    assert report["error_message"] == error
    assert set(report["output_artifacts"]) == {"output.json"}
    with tarfile.open(fileobj=io.BytesIO(client.archives[0])) as tar:
        assert "runs/node_a/worker/events.jsonl" not in tar.getnames()


def test_recovered_run_still_fails_when_artifact_upload_is_rejected(
    tmp_path: Path, evidence_root: Path
) -> None:
    class RejectingClient(QueueFakeClient):
        def upload_artifact(self, path: Path) -> str:
            raise HostRequestError("artifact upload rejected", 400)

    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _write_emergency(evidence_root, _successful_events())
    shutil.rmtree(work_root / "exec-1" / "job" / "runs" / "node_a" / "worker")
    client = RejectingClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"] == "artifact upload rejected"
    assert report["output_artifacts"] == {}

"""#1350：空事件流也用显式扫描完成状态保留已确定的执行结论。"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path
from typing import Any

import pytest

from tests.workers.upload_queue_testlib import QueueFakeClient, _execution_dir, _queue, _task
from worker import state_evidence

pytestmark = pytest.mark.no_db


@pytest.fixture
def evidence_root(tmp_path: Path) -> Path:
    root = state_evidence.configure_evidence_root(tmp_path / "state")
    try:
        yield root
    finally:
        state_evidence.reset_evidence_root()


def test_empty_events_cleanup_after_scan_preserves_completed(
    tmp_path: Path, evidence_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")
    original_add = tarfile.TarFile.add
    deleted = False

    def delete_before_run_add(self: tarfile.TarFile, name: Any, *args: Any, **kwargs: Any):
        nonlocal deleted
        if Path(name) == run_dir and not deleted:
            deleted = True
            shutil.rmtree(run_dir)
        return original_add(self, name, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "add", delete_before_run_add)
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    assert deleted
    report = client.reports[0]
    assert report["status"] == "completed", report
    assert report["exit_code"] == 0
    assert report["error_message"] == ""
    assert list(client.uploads.values()) == [b"{}"]


def test_empty_events_validation_probe_race_preserves_completed(
    tmp_path: Path, evidence_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    events = run_dir / "events.jsonl"
    events.write_text("", encoding="utf-8")
    original_probe = Path.is_file
    events_probes = 0
    deleted = False

    def delete_after_validation_probe(path: Path) -> bool:
        nonlocal events_probes, deleted
        present = original_probe(path)
        if path == events and present:
            events_probes += 1
            if events_probes == 2:
                deleted = True
                shutil.rmtree(run_dir)
        return present

    monkeypatch.setattr(Path, "is_file", delete_after_validation_probe)
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    assert events_probes == 2
    assert deleted
    report = client.reports[0]
    assert report["status"] == "completed", report
    assert report["exit_code"] == 0
    assert report["error_message"] == ""
    assert list(client.uploads.values()) == [b"{}"]

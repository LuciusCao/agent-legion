"""Unit tests for one claimed execution's lifecycle (worker/execution_run.py).

Split from tests/test_agent_worker.py to stay under the test-file line
budget; the executor's protocol/registration/hot-reload/status surface
stays there. These cases drive ``run_execution`` end-to-end with a fake
Host client, covering both kinds' shared post-exit tail (release-slot,
upload handoff, local discard) and the #203 pending-marker claim semantics.
"""

from __future__ import annotations

import hashlib
import json
import stat
import tarfile
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from server.app.agent_broker.agent_bundle import build_agent_bundle
from worker import executor as agent_worker
from worker.status import ExecutionStatusReporter
from worker.upload_queue import PENDING_FILENAME, UploadQueue


def _make_bundle(tmp_path: Path, manifest: dict) -> Path:
    skill_src = tmp_path / "skill_src"
    skill_src.mkdir(exist_ok=True)
    (skill_src / "SKILL.md").write_text("# s", encoding="utf-8")
    bundle = tmp_path / f"bundle-{len(list(tmp_path.glob('bundle-*')))}.tar.gz"
    build_agent_bundle(bundle, skill_dir=skill_src, manifest=manifest)
    return bundle


def _manifest(command: list[str], *, timeout_seconds: int = 60) -> dict:
    return {
        "command_spec": {"command": command, "prompt": "do the thing"},
        "input_artifacts": {},
        "expected_outputs": ["output.json"],
        "execution": {"timeout_seconds": timeout_seconds},
    }


def _claim(execution_id: str = "exec-1") -> dict:
    return {
        "execution_id": execution_id,
        "lease_id": "lease-1",
        "node_key": "node_a",
        "bundle_url": "/api/agent-executions/exec-1/bundle",
    }


class FakeClient:
    """In-memory stand-in for agent_worker.Client."""

    def __init__(
        self, bundle: Path, *, heartbeat_status: int = 204, release_status: int = 204
    ) -> None:
        self._bundle = bundle
        self._heartbeat_status = heartbeat_status
        self._release_status = release_status
        self.heartbeats = 0
        self.heartbeat_lease_ids: list[str] = []
        self.reports: list[dict] = []
        self.report_lease_ids: list[str] = []
        self.release_calls = 0
        self.uploads: dict[str, bytes] = {}

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._bundle.read_bytes())

    def upload_artifact(self, path: Path) -> str:
        data = path.read_bytes()
        self.uploads[hashlib.sha256(data).hexdigest()] = data
        return f"sha256:{hashlib.sha256(data).hexdigest()}"

    def get_self(self) -> dict:
        return {
            "worker_id": "w1",
            "name": "Worker 1",
            "revoked": False,
            "online": True,
        }

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        self.heartbeats += 1
        self.heartbeat_lease_ids.append(lease_id)
        return self._heartbeat_status, []

    def release_slot(self, execution_id: str, lease_id: str) -> int:
        self.release_calls += 1
        return self._release_status

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.reports.append(metadata)
        self.report_lease_ids.append(lease_id)
        return 204, b""


def _run(client: FakeClient, work_root: Path, shutdown: threading.Event | None = None) -> None:
    uploads = UploadQueue(
        client,
        ExecutionStatusReporter(None),
        max_concurrency=2,
        heartbeat_interval=0.05,
        stop=threading.Event(),
    )
    agent_worker.run_execution(
        client,
        _claim(),
        work_root,
        {},
        0.05,
        shutdown or threading.Event(),
        1,
        ExecutionStatusReporter(None),
        uploads,
        threading.Semaphore(4),
    )
    # Uploads are asynchronous now; drain the queue before asserting.
    uploads.shutdown()


def _write_executable(path: Path, body: str) -> str:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def test_run_execution_completed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_GATEWAY_TOKEN", raising=False)
    script = _write_executable(
        tmp_path / "fake_pi",
        "#!/usr/bin/env python3\nimport time\nfrom pathlib import Path\n"
        # Give the 0.05s heartbeat loop room to beat at least once before
        # the process exits; an instant-exit script makes the heartbeat
        # count assertion below timing-flaky on fast machines.
        'time.sleep(0.2)\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    _run(client, tmp_path / "work")
    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["exit_code"] == 0
    assert "output.json" in report["output_artifacts"]
    assert client.heartbeats >= 1
    # Every heartbeat and the result report carry the claimed lease_id.
    assert client.heartbeat_lease_ids and set(client.heartbeat_lease_ids) == {"lease-1"}
    assert client.report_lease_ids == ["lease-1"]


def test_run_execution_pre_spawn_failure_reports_failed(tmp_path: Path) -> None:
    client = FakeClient(_make_bundle(tmp_path, _manifest(["true"])))

    def boom(path: str, destination: Path) -> None:
        raise urllib.error.URLError("host unreachable")

    client.download = boom  # type: ignore[method-assign]
    _run(client, tmp_path / "work")
    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "failed"
    assert "host unreachable" in client.reports[0]["error_message"]


def test_run_execution_model_error_with_exit_zero_reports_failed(tmp_path: Path) -> None:
    # Pi exits 0 even when the model call fails (e.g. provider 401); the
    # worker must scan its own events file and report the real error instead
    # of "completed" with no artifacts.
    event = json.dumps(
        {
            "message": {
                "role": "assistant",
                "stopReason": "error",
                "errorMessage": "401: Authentication Fails",
            }
        }
    )
    script = _write_executable(
        tmp_path / "fake_pi",
        f"#!/usr/bin/env python3\nprint({event!r})\n",
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    _run(client, tmp_path / "work")
    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "401" in report["error_message"]


def test_run_execution_heartbeat_409_kills_run_and_skips_report(tmp_path: Path) -> None:
    script = _write_executable(
        tmp_path / "sleeper", "#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n"
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])), heartbeat_status=409)
    started = time.monotonic()
    _run(client, tmp_path / "work")
    elapsed = time.monotonic() - started
    assert client.reports == []
    assert elapsed < 20, f"ownership-lost run was not killed promptly ({elapsed:.1f}s)"


def test_run_execution_transient_heartbeat_error_keeps_beating(tmp_path: Path) -> None:
    script = _write_executable(
        tmp_path / "fake_pi",
        '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    calls = 0

    def flaky(execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        nonlocal calls
        calls += 1
        if calls % 2 == 1:
            raise urllib.error.URLError("boom")
        return 500, []

    client.heartbeat = flaky  # type: ignore[method-assign]
    _run(client, tmp_path / "work")
    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "completed"


def test_run_execution_shutdown_terminates_child_and_reports_cancelled(
    tmp_path: Path,
) -> None:
    script = _write_executable(
        tmp_path / "sleeper", "#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n"
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    shutdown = threading.Event()
    threading.Timer(0.5, shutdown.set).start()
    started = time.monotonic()
    _run(client, tmp_path / "work", shutdown=shutdown)
    elapsed = time.monotonic() - started
    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "cancelled"
    assert elapsed < 20, f"shutdown was not bounded ({elapsed:.1f}s)"


def test_run_execution_replaces_stale_execution_dir(tmp_path: Path) -> None:
    stale = tmp_path / "work" / "exec-1"
    stale.mkdir(parents=True)
    (stale / "junk").write_text("old", encoding="utf-8")
    script = _write_executable(
        tmp_path / "fake_pi",
        '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    _run(client, tmp_path / "work")
    assert client.reports[0]["status"] == "completed"


def test_run_execution_skips_claim_when_pending_upload_marker_exists(
    tmp_path: Path,
) -> None:
    """#203：restore() 恢复的 pending 结果排队中且 marker 属于当前 claim 的
    lease——run_execution 必须放弃本次 claim（不 prepare、不 release、不 report
    假 failed），目录与 marker 留给 UploadQueue 投递。"""
    execution_dir = tmp_path / "work" / "exec-1"
    job_dir = execution_dir / "job"
    job_dir.mkdir(parents=True)
    (job_dir / "output.json").write_text("old result", encoding="utf-8")
    marker = execution_dir / PENDING_FILENAME
    marker.write_text(
        '{"version": 1, "execution_id": "exec-1", "lease_id": "lease-1"}', encoding="utf-8"
    )
    script = _write_executable(
        tmp_path / "fake_pi",
        '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("output.json").write_text("new", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))

    _run(client, tmp_path / "work")

    # 不上报（也没有假 failed）、不 release：租约到期由 Host 重新调度。
    assert client.reports == []
    assert client.release_calls == 0
    # 目录、marker 与已准备的产物原样保留。
    assert marker.is_file()
    assert (job_dir / "output.json").read_text(encoding="utf-8") == "old result"


def test_run_execution_runs_when_marker_belongs_to_stale_lease(
    tmp_path: Path,
) -> None:
    """#203 P1：孤儿 marker（旧 lease，report 必 409）不得牺牲当前 attempt。
    claim 每次 attempt+1 且 sweeper 超过 requeue_limit 就不再重排——若仅凭
    marker 存在就跳过，最后一次允许的 claim 会让节点直接以「requeue limit
    exceeded」失败，而旧上传又因 lease 不匹配只能收 409，两边都输。所以
    本次 claim 照常执行：清掉孤儿目录、跑完并上报本次结果。"""
    execution_dir = tmp_path / "work" / "exec-1"
    job_dir = execution_dir / "job"
    job_dir.mkdir(parents=True)
    (job_dir / "output.json").write_text("dead result", encoding="utf-8")
    marker = execution_dir / PENDING_FILENAME
    marker.write_text(
        '{"version": 1, "execution_id": "exec-1", "lease_id": "lease-old"}', encoding="utf-8"
    )
    script = _write_executable(
        tmp_path / "fake_pi",
        '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))

    _run(client, tmp_path / "work")

    # 本次 claim 完整走完：release + report completed。
    assert client.release_calls == 1
    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "completed"
    # 投递成功后整个目录（含孤儿 marker）被 _report 清掉。
    assert not execution_dir.exists()


def test_run_execution_releases_slot_after_process_exit(tmp_path: Path) -> None:
    script = _write_executable(
        tmp_path / "fake_pi",
        '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    _run(client, tmp_path / "work")
    assert client.release_calls == 1
    assert client.reports[0]["status"] == "completed"
    # Successful delivery removes the execution dir entirely.
    assert not (tmp_path / "work" / "exec-1").exists()


def test_run_execution_release_404_falls_back_to_inline_ownership(tmp_path: Path) -> None:
    # Host predates release-slot: the upload must still be delivered.
    script = _write_executable(
        tmp_path / "fake_pi",
        '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])), release_status=404)
    _run(client, tmp_path / "work")
    assert client.release_calls == 1
    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "completed"


def test_run_execution_release_409_discards_result(tmp_path: Path) -> None:
    # Lease already gone (Host swept/requeued): the result is moot — never
    # upload it, and clean the execution dir locally.
    script = _write_executable(
        tmp_path / "fake_pi",
        '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])), release_status=409)
    _run(client, tmp_path / "work")
    assert client.release_calls == 1
    assert client.reports == []
    assert not (tmp_path / "work" / "exec-1").exists()


def test_run_execution_compresses_events_before_report(tmp_path: Path) -> None:
    # Streaming deltas are dropped so the uploaded archive (and the local
    # copy) stay small; snapshots needed by the host log renderer are kept.
    delta = json.dumps({"type": "message_update", "delta": "thinking_delta", "text": "x"})
    snapshot = json.dumps({"type": "message_end", "message": {"role": "assistant"}})
    script = _write_executable(
        tmp_path / "fake_pi",
        f"#!/usr/bin/env python3\nprint({delta!r})\nprint({snapshot!r})\n",
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    captured: list[str] = []
    original_report = client.report

    def report_and_capture(execution_id, lease_id, metadata, archive):  # type: ignore[no-untyped-def]
        with tarfile.open(archive, "r:gz") as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("events.jsonl"))
            extracted = tar.extractfile(member)
            assert extracted is not None
            captured.append(extracted.read().decode("utf-8"))
        original_report(execution_id, lease_id, metadata, archive)

    client.report = report_and_capture  # type: ignore[method-assign]
    _run(client, tmp_path / "work")
    assert client.reports[0]["status"] == "completed"
    assert len(captured) == 1
    assert "message_end" in captured[0]
    assert "thinking_delta" not in captured[0]


def test_run_execution_publishes_status_and_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_GATEWAY_TOKEN", raising=False)
    status_path = tmp_path / "state" / "current_executions.json"
    status_path.parent.mkdir(parents=True)
    captured = tmp_path / "captured.json"
    script = _write_executable(
        tmp_path / "fake_pi",
        "#!/usr/bin/env python3\n"
        "import shutil, sys\n"
        "from pathlib import Path\n"
        "shutil.copy(sys.argv[1], sys.argv[2])\n"
        "Path('output.json').write_text('{}', encoding='utf-8')\n",
    )
    client = FakeClient(
        _make_bundle(tmp_path, _manifest([script, str(status_path), str(captured)]))
    )
    reporter = ExecutionStatusReporter(status_path)
    uploads = UploadQueue(
        client,
        reporter,
        max_concurrency=2,
        heartbeat_interval=0.05,
        stop=threading.Event(),
    )
    agent_worker.run_execution(
        client,
        _claim(),
        tmp_path / "work",
        {},
        0.05,
        threading.Event(),
        1,
        reporter,
        uploads,
        threading.Semaphore(4),
    )
    uploads.shutdown()
    snapshot = json.loads(captured.read_text(encoding="utf-8"))
    assert snapshot["executions"]["exec-1"]["phase"] == "running"
    assert snapshot["executions"]["exec-1"]["node_key"] == "node_a"
    assert snapshot["executions"]["exec-1"]["started_at"]
    assert json.loads(status_path.read_text(encoding="utf-8"))["executions"] == {}

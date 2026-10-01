"""Upload-handoff / attempt-reclaim lifecycle tests for one claimed execution
(worker/execution/run.py).

Split from tests/workers/test_execution_run.py when it crossed the 800-line
test-file budget (#779 codex train review R3); cases migrated verbatim.
Shared fakes/builders live in tests/workers/execution_run_testlib.py.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tests.workers.execution_run_testlib import (
    FakeClient,
    _claim,
    _make_bundle,
    _manifest,
    _run,
    _write_executable,
)
from worker.execution import run as execution_run_module
from worker.execution.heartbeat_batch import BatchHeartbeatRegistry, batch_heartbeat_loop
from worker.execution.ownership import execution_mutex
from worker.execution.run import run_execution
from worker.status import ExecutionStatusReporter
from worker.upload.queue import UploadQueue


class _StatusCapture:
    """Capture the status.start kwargs run_execution hands the reporter."""

    def __init__(self) -> None:
        self.fields: dict | None = None

    def start(self, execution_id: str, **fields: object) -> None:
        self.fields = dict(fields)

    def set_phase(self, execution_id: str, phase: str) -> None:
        pass

    def upsert_phase(self, execution_id: str, phase: str, **fields: object) -> None:
        pass

    def finish(self, execution_id: str) -> None:
        pass


def test_run_execution_rechecks_incoming_lease_after_upload_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new claim lost during handoff must not download or start its job."""
    client = FakeClient(_make_bundle(tmp_path, _manifest(["true"])))
    downloads = 0
    original_download = client.download

    def counting_download(path: str, destination: Path) -> None:
        nonlocal downloads
        downloads += 1
        original_download(path, destination)

    client.download = counting_download  # type: ignore[method-assign]
    heartbeats = []
    real_start = execution_run_module.start_lease_heartbeat

    def capture_heartbeat(*args: object, **kwargs: object):
        heartbeat = real_start(*args, **kwargs)  # type: ignore[arg-type]
        heartbeats.append(heartbeat)
        return heartbeat

    monkeypatch.setattr(execution_run_module, "start_lease_heartbeat", capture_heartbeat)

    class LosingHandoff:
        submitted = False

        def wait_for_prior_upload(
            self,
            execution_id: str,
            lease_id: str,
            stop: threading.Event,
            ownership_lost: threading.Event,
        ) -> bool:
            ownership_lost.set()  # verdict races the old uploader's completion
            return True

        def submit(self, task: object) -> None:
            self.submitted = True

    uploads = LosingHandoff()
    status = _StatusCapture()
    run_execution(
        client,
        _claim(),
        tmp_path / "work",
        {},
        0.05,
        threading.Event(),
        1,
        status,  # type: ignore[arg-type]
        uploads,  # type: ignore[arg-type]
        threading.Semaphore(4),
    )

    assert downloads == 0
    assert status.fields is None
    assert not uploads.submitted
    assert heartbeats and heartbeats[0].stop.is_set()


def test_reclaim_serializes_old_attempt_teardown_and_new_attempt(tmp_path: Path) -> None:
    """#564 复现：Host 误判 lease-1 过期并重排队，同一 worker 立刻重新
    claim 同一 execution_id（lease-2），旧 attempt 线程仍存活。修复前两
    个 attempt 并发交错：旧 attempt 的丢弃收尾 rmtree 删掉新 attempt 刚
    重建、正在使用的目录（prompt.md FileNotFoundError 的根源）。修复后
    per-execution 互斥锁把「旧收尾」与「新 prepare」串行——新 attempt
    的下载必须发生在旧 attempt 线程完全退出之后，且新 attempt 正常完成。

    实测的修复前失败通道：旧 attempt 因 BatchHeartbeatRegistry.register
    覆盖串话（同 execution_id 的新注册覆盖旧表项）收不到自己的 409，
    sleeper 不被杀、本测试挂到 join 超时，而非直接观测到 rmtree/prompt.md
    交错；另一次复现则表现为 upload pending marker 原子写撞上 rmtree 的
    FileNotFoundError——同一竞态的不同投影。互斥锁顺带消除了注册表覆盖
    串话（同 execution_id 的两个 attempt 不再并发注册）。
    """
    work_root = tmp_path / "work"
    sleeper = _write_executable(
        tmp_path / "sleeper", "#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n"
    )
    finisher = _write_executable(
        tmp_path / "finisher",
        "#!/usr/bin/env python3\nimport time\nfrom pathlib import Path\n"
        'time.sleep(0.2)\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    bundle_a = _make_bundle(tmp_path, _manifest([sleeper]))
    bundle_b = _make_bundle(tmp_path, _manifest([finisher]))

    class ReclaimClient(FakeClient):
        """lease-1 的批量心跳在主线程武装后判 lost（Host 已重排），lease-2 正常续期。"""

        def __init__(self) -> None:
            super().__init__(bundle_a)
            self.a_done = threading.Event()
            # 判死武装：主线程观测到 prompt.md（attempt A 进入运行态）后才
            # 允许心跳把 lease-1 判 lost。心跳协调器先于 thread_a 启动，
            # 高负载下首批心跳可能抢在 A 通过 wait_for_prior_upload 的
            # ownership 复查之前判死 lease-1，A 提前放弃、prompt.md 永不
            # 出现——主线程只能在 10s Deadline 报 "never reached running"。
            self.lost_armed = threading.Event()
            self.download_saw_a_done: list[bool] = []

        def download(self, path: str, destination: Path) -> None:
            self.download_saw_a_done.append(self.a_done.is_set())
            bundle = bundle_a if len(self.download_saw_a_done) == 1 else bundle_b
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(bundle.read_bytes())

        def heartbeat_batch(
            self, executions: list[tuple[str, str]]
        ) -> tuple[int, dict[str, list[str]]]:
            lost = [
                eid for eid, lease in executions if lease == "lease-1" and self.lost_armed.is_set()
            ]
            renewed = [eid for eid, _ in executions if eid not in lost]
            return 200, {"renewed": renewed, "lost": lost, "cancelled_execution_ids": []}

    client = ReclaimClient()
    registry = BatchHeartbeatRegistry()
    stop = threading.Event()
    coordinator = threading.Thread(
        target=batch_heartbeat_loop, args=(client, registry, stop, 0.05), daemon=True
    )
    coordinator.start()

    def run_one(claim: dict) -> None:
        uploads = UploadQueue(
            client,
            ExecutionStatusReporter(None),
            max_concurrency=2,
            heartbeat_interval=0.05,
            stop=threading.Event(),
            heartbeat_registry=registry,
        )
        try:
            run_execution(
                client,
                claim,
                work_root,
                {},
                0.05,
                threading.Event(),
                1,
                ExecutionStatusReporter(None),
                uploads,
                threading.Semaphore(4),
                registry,
            )
        finally:
            uploads.shutdown()

    claim_a = {**_claim(), "lease_id": "lease-1"}
    claim_b = {**_claim(), "lease_id": "lease-2"}

    def run_a() -> None:
        try:
            run_one(claim_a)
        finally:
            client.a_done.set()

    thread_a = threading.Thread(target=run_a)
    thread_a.start()
    # 等 attempt A 完成 prepare 进入运行态（prompt.md 已写、agent 在跑），
    # 再启动 attempt B——此时 A 正持有执行目录。
    prompt = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker" / "prompt.md"
    deadline = time.monotonic() + 10
    while not prompt.is_file():
        assert time.monotonic() < deadline, "attempt A never reached running phase"
        time.sleep(0.01)
    # A 已进入运行态，此刻起允许心跳把 lease-1 判 lost（ reclaim 剧情开始）。
    client.lost_armed.set()
    thread_b = threading.Thread(target=run_one, args=(claim_b,))
    thread_b.start()
    thread_a.join(timeout=30)
    thread_b.join(timeout=30)
    stop.set()
    coordinator.join(timeout=2)
    assert not thread_a.is_alive()
    assert not thread_b.is_alive()

    # 串行化钉：B 的下载（prepare 的第一步）必须发生在 A 的 run_execution
    # 完全退出（含丢弃收尾）之后；修复前无锁，两次 attempt 的下载并发交错。
    assert client.download_saw_a_done == [False, True]
    # A 的 lease 已丢、不上报；B 正常跑完并投递 completed。
    assert client.report_lease_ids == ["lease-2"]
    assert client.reports[0]["status"] == "completed"


def test_run_execution_abandons_claim_when_attempt_mutex_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#564 P2：等锁有界——锁被占满等到上界时新 attempt 放弃本次 claim：
    不 download、不 prepare、不上报、不启动心跳，已存在的目录原样保留，
    租约过期后由 Host 在 worker 恢复健康时重排。"""
    monkeypatch.setattr("worker.execution.run.MUTEX_WAIT_BOUND_SECONDS", 0.2)
    client = FakeClient(_make_bundle(tmp_path, _manifest(["true"])))
    downloads = 0
    original_download = client.download

    def counting_download(path: str, destination: Path) -> None:
        nonlocal downloads
        downloads += 1
        original_download(path, destination)

    client.download = counting_download  # type: ignore[method-assign]
    # 哨兵文件：放弃路径不得触碰目录（目录可能正被持锁的旧 attempt 使用）。
    execution_dir = tmp_path / "work" / "exec-1"
    execution_dir.mkdir(parents=True)
    sentinel = execution_dir / "sentinel"
    sentinel.write_text("untouched", encoding="utf-8")

    # threading.Lock 不可重入：同线程持锁即可模拟「旧 attempt 占着锁」。
    with execution_mutex("exec-1"):
        started = time.monotonic()
        _run(client, tmp_path / "work")
        elapsed = time.monotonic() - started

    assert elapsed < 30  # 生产上界 60s；monkeypatch 后实际 ~0.2s
    assert downloads == 0
    assert client.reports == []
    assert client.release_calls == 0
    assert sentinel.read_text(encoding="utf-8") == "untouched"


def test_claim_status_fields_key_on_workspace_id(tmp_path: Path) -> None:
    """#211 Phase 2: the local status record keys on workspace_id — the
    claim's workflow_key is deprecated (equal since v62) and is no longer
    mirrored into the observation file."""
    script = _write_executable(
        tmp_path / "fake_pi", '#!/usr/bin/env python3\nopen("output.json","w").write("{}")\n'
    )
    capture = _StatusCapture()
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    uploads = UploadQueue(
        client, capture, max_concurrency=2, heartbeat_interval=0.05, stop=threading.Event()
    )
    claim = {
        "execution_id": "exec-1",
        "lease_id": "lease-1",
        "workspace_id": "ws-1",
        "workflow_key": "ws-1",
        "job_id": "job-1",
        "node_key": "node_a",
        "agent_id": "agent-1",
        "bundle_url": "/api/agent-executions/exec-1/bundle",
    }
    run_execution(
        client,
        claim,
        tmp_path / "work",
        {},
        0.05,
        threading.Event(),
        1,
        capture,
        uploads,
        threading.Semaphore(4),
    )
    uploads.shutdown()

    assert capture.fields is not None
    assert capture.fields["workspace_id"] == "ws-1"
    assert "workflow_key" not in capture.fields
    assert capture.fields["job_id"] == "job-1"

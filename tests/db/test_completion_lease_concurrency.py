"""同 lease 并发 /result 的收尾串行测试（codex #774 P1 族 + #759 复审 P2）。

自 tests/db/test_completion_generation_gates.py 拆出（800 行拆分线，用例
零改动迁移）：钉「同一 lease 的并发结果提交按 lease 串行」——镜像登记
走 finish 前的 lease 写闸、文件落盘走 finish 内的代次闸，两道闸的胜者
必须同源；解包失败的收尾同样在该临界区内（#759 复审 P2），否则失败
finish 会在锁外抢跑释放 lease，与在途成功路径三面分裂。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.agent_control.completion import AgentOutcome
from server.app.jobs import JobQueries
from tests.db.completion_helpers import (
    _completion_handler,
    _node_row,
    _result_archive,
    _seed_completion_job,
)
from tests.fakes.storage import FakeObjectStorage


def _start(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["result"] = fn()
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=30)
    assert not thread.is_alive(), "concurrent finish never resolved"


def test_concurrent_duplicate_result_finishes_never_flip_node_to_failed(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """同一 execution 的两个并发 /result 重试收敛到 completed：赢家的
    staging 源删除发生在 finish **提交之后**（promote→finish 窗口内绝不
    删，否则后到者的 HEAD 核验撞见虚假存储故障，失败 finish 抢跑冤判
    已完成节点）；输家重复 promote 幂等、finish 竞争落败为零副作用
    （409 语义）。本用例钉死两条不变量：任意交错下节点 completed、双
    finish 结束后 staging 源已被赢家清理（删除点确实在 finish 之后且
    只删一次不炸）。"""
    _seed_completion_job(job_db, workspace_id="gate24-ws", job_id="gate24-job")
    storage = FakeObjectStorage()
    staging_key = "jobs-staging/gate24-ws/gate24-job/exec-1/out.json"
    storage.objects[staging_key] = b"ref-bytes"
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate24-ws" / "gate24-job"
    job_dir.mkdir(parents=True)
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {})

    def _finish() -> bool:
        return handler.finish(
            lease_id="lease-1",
            worker_id="worker-1",
            job_id="gate24-job",
            node_key="node_a",
            manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
            outcome=AgentOutcome(
                status="completed",
                exit_code=0,
                output_artifacts={
                    "out.json": {
                        "storage_key": staging_key,
                        "size_bytes": 9,
                        "content_hash": "",
                    }
                },
            ),
            archive_name="result.tar.gz",
        )

    thread_a, outcome_a = _start(_finish)
    thread_b, outcome_b = _start(_finish)
    _join(thread_a)
    _join(thread_b)

    assert outcome_a.get("error") is None
    assert outcome_b.get("error") is None
    assert _node_row("gate24-job", "node_a")["status"] == "completed"
    assert (job_dir / "out.json").read_bytes() == b"ref-bytes"
    # 赢家在 finish 提交后删了 staging 源（重复删除幂等、不炸）；authority
    # 字节与清单行不受并发重试影响。
    assert staging_key not in storage.objects
    assert storage.objects["jobs/gate24-ws/gate24-job/out.json"] == b"ref-bytes"
    row = store.row_for_node("gate24-job", "node_a", "out.json")
    assert row is not None
    assert row["size_bytes"] == 9


class _BarrierTimer:
    """ResultStageTimer 的最小替身：在指定 stage 名处停下等主线程放行。"""

    def __init__(self, gate_stage: str) -> None:
        self._gate_stage = gate_stage
        self.entered = threading.Event()
        self.release = threading.Event()

    def stage(self, name: str) -> None:
        if name == self._gate_stage:
            self.entered.set()
            # 等不到放行即红（不静默退化为无序交错）。
            assert self.release.wait(timeout=10), "main thread never released the stage gate"


def test_concurrent_same_lease_results_commit_only_the_finish_winner(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """codex #774 P1：同 lease 两个并发 /result（同名不同字节归档）。镜像登
    记走 finish 前的 lease 写闸、文件落盘走 finish 内的代次闸——两道闸的
    胜者可以不同：A 镜像、B 镜像、A finish 获胜时本地面=A、权威面/清单
    面=B 永久分叉（local-first 读 A，淘汰后 S3 读 B）。按 lease 串行后到
    者的镜像写闸看到已释放的 lease 直接拒写，所有面只剩获胜者。

    交错构造：A、B 都在镜像之后（``artifacts_upload`` stage）停下等放行；
    串行锁下 B 到不了该 stage（卡在锁上），主线程先放 A 完成 finish（获
    胜）、再放 B。突变自检（去掉 completion_locks.acquire）：B 的镜像在
    A finish 前完成，authority/清单=B 而 job_dir=A——三条同源断言全红。"""
    import hashlib

    _seed_completion_job(job_db, workspace_id="dup-ws", job_id="dup-job")
    storage = FakeObjectStorage()
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "dup-ws" / "dup-job"
    job_dir.mkdir(parents=True)
    bundles = tmp_path / "bundles"
    _result_archive(bundles / "result-a.tar.gz", {"out.json": b"bytes-a"})
    _result_archive(bundles / "result-b.tar.gz", {"out.json": b"bytes-b"})
    timer_a = _BarrierTimer("artifacts_upload")
    timer_b = _BarrierTimer("artifacts_upload")

    def _finish(archive_name: str, timer: _BarrierTimer) -> Any:
        return handler.finish(
            lease_id="lease-1",
            worker_id="worker-1",
            job_id="dup-job",
            node_key="node_a",
            manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
            outcome=AgentOutcome(
                status="completed",
                exit_code=0,
                output_artifacts={"out.json": "sha256:deadbeef"},
            ),
            archive_name=archive_name,
            stage_timer=timer,  # type: ignore[arg-type]
        )

    thread_a, outcome_a = _start(lambda: _finish("result-a.tar.gz", timer_a))
    assert timer_a.entered.wait(timeout=10)  # A 镜像完成、finish 前停下
    thread_b, outcome_b = _start(lambda: _finish("result-b.tar.gz", timer_b))
    assert not timer_b.entered.wait(timeout=1.0)  # B 卡在临界区外（未进镜像后段）
    timer_a.release.set()  # A finish 获胜
    _join(thread_a)
    assert outcome_a.get("error") is None
    assert outcome_a["result"] is True
    timer_b.release.set()  # B 进临界区：镜像写闸已随 lease 释放关闭
    _join(thread_b)
    assert outcome_b.get("error") is None
    assert outcome_b["result"] is False  # 迟到 finish = 409 语义

    assert storage.objects["jobs/dup-ws/dup-job/out.json"] == b"bytes-a"  # 权威面=获胜者
    assert (job_dir / "out.json").read_bytes() == b"bytes-a"  # 本地面=获胜者
    row = store.row_for_node("dup-job", "node_a", "out.json")
    assert row is not None
    assert row["content_hash"] == hashlib.sha256(b"bytes-a").hexdigest()  # 清单面=获胜者


def test_concurrent_unpack_failure_finish_shares_lease_critical_section(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """codex 复审 P2（#759）：同 lease 并发 /result，A 正常解包进 finish_staged
    临界区（镜像已完成、finish 前停下），B 的归档损坏解包失败——B 的失败
    收尾必须与成功路径共用同一 lease 临界区：B 阻塞到 A 的 finish 提交之
    后，迟到 finish 拿 409 语义（False），节点保持 completed、三面全是 A。

    修复前 B 在锁外直接 finish：lease 被 B 释放、失败结果提交（节点翻
    failed），A 随后 finish 得 409 False——获胜的失败结果与 A 已登记的镜
    像/清单/authority 面来自不同请求（三面分裂）。突变自检：把失败臂的
    finish 移出 completion_locks 后本测试红（B 不被阻塞、节点 failed）。"""
    import hashlib

    _seed_completion_job(job_db, workspace_id="unpack-ws", job_id="unpack-job")
    storage = FakeObjectStorage()
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "unpack-ws" / "unpack-job"
    job_dir.mkdir(parents=True)
    bundles = tmp_path / "bundles"
    _result_archive(bundles / "result-a.tar.gz", {"out.json": b"bytes-a"})
    bundles.mkdir(parents=True, exist_ok=True)
    (bundles / "result-b.tar.gz").write_bytes(b"definitely not a gzip tarball")
    timer_a = _BarrierTimer("artifacts_upload")

    def _finish_ok() -> Any:
        return handler.finish(
            lease_id="lease-1",
            worker_id="worker-1",
            job_id="unpack-job",
            node_key="node_a",
            manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
            outcome=AgentOutcome(
                status="completed",
                exit_code=0,
                output_artifacts={"out.json": "sha256:deadbeef"},
            ),
            archive_name="result-a.tar.gz",
            stage_timer=timer_a,  # type: ignore[arg-type]
        )

    def _finish_corrupt() -> Any:
        return handler.finish(
            lease_id="lease-1",
            worker_id="worker-1",
            job_id="unpack-job",
            node_key="node_a",
            manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
            outcome=AgentOutcome(
                status="completed",
                exit_code=0,
                output_artifacts={"out.json": "sha256:deadbeef"},
            ),
            archive_name="result-b.tar.gz",
        )

    thread_a, outcome_a = _start(_finish_ok)
    assert timer_a.entered.wait(timeout=10)  # A 持锁：镜像完成、finish 前停下
    thread_b, outcome_b = _start(_finish_corrupt)
    # B 的失败收尾在临界区外排队：A 放行前 B 不得完成 finish（修复前 B 在
    # 锁外直接提交失败结果，此断言即红——轮询以消除单点 sleep 假绿）。
    deadline = threading.Event()
    assert not deadline.wait(timeout=1.0)
    assert "result" not in outcome_b, "unpack-failure finish bypassed the lease critical section"
    timer_a.release.set()  # A finish 获胜并提交
    _join(thread_a)
    assert outcome_a.get("error") is None
    assert outcome_a["result"] is True
    _join(thread_b)
    assert outcome_b.get("error") is None
    assert outcome_b["result"] is False  # 迟到的失败 finish = 409 语义，不翻节点

    assert _node_row("unpack-job", "node_a")["status"] == "completed"
    assert (job_dir / "out.json").read_bytes() == b"bytes-a"  # 本地面=获胜者
    assert storage.objects["jobs/unpack-ws/unpack-job/out.json"] == b"bytes-a"  # 权威面
    row = store.row_for_node("unpack-job", "node_a", "out.json")
    assert row is not None
    assert row["content_hash"] == hashlib.sha256(b"bytes-a").hexdigest()  # 清单面

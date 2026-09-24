"""completion 层与批 finish 的代次闸交错用例（#759 复审 P1-1、对抗复审 N4）。

自 tests/db/test_generation_write_gates.py 拆出（800 行拆分线）：这里钉
Worker 结果归档「只解包到 staging、文件提升挤进 finish 代次 CAS」的
completion 层语义，以及批 finish（finish_many）的 stale events 族门。
交错手法与姊妹文件一致：直接 SQL bump 模拟已提交 reset，不用裸 sleep。
"""

from __future__ import annotations

import io
import tarfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from server.app.agent_control.completion import AgentCompletionHandler, AgentOutcome
from server.app.db.transaction import write_transaction
from server.app.executors import _lease_lifecycle
from server.app.executors._lease_finish_batch import finish_many
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult
from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL


def _node_row(job_id: str, node_key: str) -> dict[str, Any]:
    with write_transaction(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select status, execution_generation from job_nodes where job_id=%s and node_key=%s",
            (job_id, node_key),
        ).fetchone()
    assert row is not None
    return dict(row)


def _node_error(job_id: str, node_key: str) -> str:
    with write_transaction(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select error_message from job_nodes where job_id=%s and node_key=%s",
            (job_id, node_key),
        ).fetchone()
    assert row is not None
    return str(row["error_message"])


class _StubArtifactStore:
    def __init__(self) -> None:
        self.refs: list[tuple[str, str, str, str]] = []

    def add_ref(self, job_id: str, node_key: str, name: str, ref: str) -> None:
        self.refs.append((job_id, node_key, name, ref))


def _seed_completion_job(
    job_db: JobQueries, *, workspace_id: str, job_id: str, lease_id: str = "lease-1"
) -> None:
    """completion 层用例的种子：带 storage_dir 的 job + running 节点 + active
    lease（executor_id 'agent:worker-1'，走 Agent broker 完成路径）。"""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, 'ws', 'demo_workflow')"
            " on conflict (id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, storage_dir)"
            " values (%s, %s, 's', 's1', %s)",
            (job_id, workspace_id, f"jobs/{workspace_id}/{job_id}"),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, 'node_a')", (job_id,))
        cursor = conn.execute(
            "insert into node_runs(job_id, node_key, status, command_json, log_path,"
            " run_dir, session_dir, started_at)"
            " values (%s, 'node_a', 'running', '[]', '', '', '', current_timestamp) returning id",
            (job_id,),
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id,"
            " job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at)"
            " values (%s, %s, 'agent:worker-1', %s, %s, 'node_a', %s,"
            " 'active', current_timestamp, current_timestamp,"
            " current_timestamp + interval '1 hour')",
            (lease_id, f"exec-{lease_id}", workspace_id, job_id, cursor.fetchone()["id"]),
        )


def _result_archive(archive: Path, members: dict[str, bytes]) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))


def _completion_handler(
    job_db: JobQueries, tmp_path: Path, storage: FakeObjectStorage
) -> tuple[AgentCompletionHandler, JobArtifactObjectStore, Path]:
    jobs_dir = tmp_path / "jobs"
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    handler = AgentCompletionHandler(
        ExecutorLeaseRepository(job_db, data_dir=tmp_path),
        _StubArtifactStore(),  # type: ignore[arg-type]
        jobs_dir,
        tmp_path / "bundles",
        skill_manager=None,
        object_store=store,
    )
    return handler, store, jobs_dir


def _finish_with_archive(handler: AgentCompletionHandler, *, job_id: str) -> bool:
    return handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id=job_id,
        node_key="node_a",
        manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={"out.json": "sha256:deadbeef"},
        ),
        archive_name="result.tar.gz",
    )


def test_completion_lands_archive_outputs_and_mirror_under_current_generation(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """对照组：代次一致——finish 闸内把归档输出提升进 job_dir、镜像带 lease
    过闸登记清单行，节点 completed，三面一致（本地文件 / 清单行 / authority
    字节）。"""
    _seed_completion_job(job_db, workspace_id="gate10-ws", job_id="gate10-job")
    storage = FakeObjectStorage()
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate10-ws" / "gate10-job"
    job_dir.mkdir(parents=True)
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"out.json": b'{"fresh": true}'})

    assert _finish_with_archive(handler, job_id="gate10-job") is True

    assert (job_dir / "out.json").read_bytes() == b'{"fresh": true}'
    row = store.row_for_node("gate10-job", "node_a", "out.json")
    assert row is not None
    assert storage.objects["jobs/gate10-ws/gate10-job/out.json"] == b'{"fresh": true}'
    assert _node_row("gate10-job", "node_a")["status"] == "completed"
    assert not list(job_dir.glob(".result-staging-*"))  # staging 已清


def test_completion_stale_finish_never_lands_bytes_or_rows(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """P1-1 核心交错：reset 在旧 Worker 迟到回报之前已 bump 代次——归档解包
    只到 staging，finish 代次 CAS 跳过提升与翻转，镜像闸关闭不登记：
    新现场的 job_dir 旧字节不被覆盖、清单零登记、authority 零写入。"""
    _seed_completion_job(job_db, workspace_id="gate11-ws", job_id="gate11-job")
    storage = FakeObjectStorage()
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate11-ws" / "gate11-job"
    job_dir.mkdir(parents=True)
    (job_dir / "in.json").write_bytes(b"current-generation-input")
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"out.json": b"stale-epoch-bytes"})
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set execution_generation=execution_generation+1 where id=%s",
            ("gate11-job",),
        )

    assert _finish_with_archive(handler, job_id="gate11-job") is True

    assert (job_dir / "in.json").read_bytes() == b"current-generation-input"
    assert not (job_dir / "out.json").exists()  # 旧代次字节从未落盘
    assert store.row_for_node("gate11-job", "node_a", "out.json") is None  # 镜像闸关闭
    assert storage.objects == {}  # authority 零写入
    node = _node_row("gate11-job", "node_a")
    assert node["status"] == "pending"  # 翻转被 CAS 跳过
    assert not list(job_dir.glob(".result-staging-*"))


def test_completion_stale_finish_never_persists_view_probed_run_dir(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """codex #774 P2：reset 落在暂存与 finish 取锁之间时 staged moves 全部
    跳过，但 run_dir 是视图探出的 job_dir 路径——事件文件从未落盘、临时
    视图随后被清理，持久化它会留下一个 404（甚至被复用后指向别次执行日
    志）的路径。闸内把它置空回退文件系统派生：只记录真实存在的路径。"""
    _seed_completion_job(job_db, workspace_id="gate27-ws", job_id="gate27-job")
    storage = FakeObjectStorage()
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate27-ws" / "gate27-job"
    job_dir.mkdir(parents=True)
    _result_archive(
        tmp_path / "bundles" / "result.tar.gz",
        {"out.json": b"stale-epoch-bytes", "runs/node_a/worker/events.jsonl": b"{}\n"},
    )
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set execution_generation=execution_generation+1 where id=%s",
            ("gate27-job",),
        )

    finished = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="gate27-job",
        node_key="node_a",
        manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={"out.json": "sha256:deadbeef"},
            run_dir="runs/node_a/worker",
        ),
        archive_name="result.tar.gz",
    )

    assert finished is True
    with write_transaction(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select run_dir from node_runs where job_id=%s and node_key='node_a'",
            ("gate27-job",),
        ).fetchone()
    assert row is not None
    assert row["run_dir"] == ""  # 未落盘的路径不持久化（文件系统派生也找不到）
    assert not (job_dir / "out.json").exists()  # 与 gate11 同语义：旧代次零落盘


def test_completion_failed_refs_still_lands_node_log(job_db: JobQueries, tmp_path: Path) -> None:
    """#759 对抗复审 P3-2：dict-ref 校验失败（staging 对象缺失）→ 结果翻
    failed，但归档里的 node.log 仍随失败 finish 的 staged_file_moves 落盘
    （staging 化前的可观测性语义），代次闸照常生效。"""
    _seed_completion_job(job_db, workspace_id="gate14-ws", job_id="gate14-job")
    storage = FakeObjectStorage()
    handler, _store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate14-ws" / "gate14-job"
    job_dir.mkdir(parents=True)
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"node.log": b"partial log"})

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="gate14-job",
        node_key="node_a",
        manifest={
            "kind": "code",
            "log_path": "logs/jobs/gate14-job/node_a.log",
            "expected_outputs": ["out.json"],
            "execution_id": "exec-1",
        },
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={
                "out.json": {
                    "storage_key": "jobs-staging/gate14-ws/gate14-job/exec-1/out.json",
                    "size_bytes": 3,
                    "content_hash": "",
                }
            },
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert (tmp_path / "logs" / "jobs" / "gate14-job" / "node_a.log").read_bytes() == b"partial log"
    assert _node_row("gate14-job", "node_a")["status"] == "failed"
    assert not (job_dir / "out.json").exists()  # 校验失败，输出不落地


def test_finish_many_skips_events_only_for_stale_entries(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#759 对抗复审 N4：批路径的 events 族门——同批内 stale 条目跳过
    token capture / PI compression（run_dir 可能已属新代次），current
    条目照常。"""
    calls: list[str] = []
    monkeypatch.setattr(
        "server.app.services.token_usage_lease.capture_token_usage_after_lease_finish",
        lambda *args, **kwargs: calls.append("capture"),
    )
    monkeypatch.setattr(
        "shared.pi_events.compress_pi_events",
        lambda *args, **kwargs: calls.append("compress"),
    )
    run_dirs: dict[str, str] = {}
    for suffix in ("a", "b"):
        workspace_id = f"gate15{suffix}-ws"
        job_id = f"gate15{suffix}-job"
        _seed_completion_job(
            job_db, workspace_id=workspace_id, job_id=job_id, lease_id=f"lease-{suffix}"
        )
        run_dirs[suffix] = f"jobs/{workspace_id}/{job_id}/runs/node_a"
        events = tmp_path / run_dirs[suffix] / "events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text("{}\n", encoding="utf-8")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set execution_generation=execution_generation+1 where id=%s",
            ("gate15a-job",),
        )
    repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)

    outcomes, callbacks = finish_many(
        repo,
        [
            (
                f"lease-{suffix}",
                ExecutionResult(status="completed", exit_code=0, run_dir=run_dirs[suffix]),
                None,
            )
            for suffix in ("a", "b")
        ],
    )
    for callback in callbacks:
        if callback is not None:
            callback()

    assert outcomes == [True, True]
    assert calls == ["capture", "compress"]  # 只有 gate15b（current）的条目


def test_completion_ref_channel_wins_over_duplicate_archive_member(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """#759 对抗复审 N2：冗余 Worker 把 out.json 同时放进归档与 dict-ref——
    ref 通道三面胜出（job_dir 本地文件 / 清单行 / authority 字节全是 ref
    字节），归档同名片段从 finish 提升面剔除，恢复 staging 化前「最后写
    胜出」的序。"""
    import hashlib

    _seed_completion_job(job_db, workspace_id="gate16-ws", job_id="gate16-job")
    storage = FakeObjectStorage()
    staging_key = "jobs-staging/gate16-ws/gate16-job/exec-1/out.json"
    storage.objects[staging_key] = b"ref-bytes"
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate16-ws" / "gate16-job"
    job_dir.mkdir(parents=True)
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"out.json": b"archive-bytes"})

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="gate16-job",
        node_key="node_a",
        manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={
                "out.json": {"storage_key": staging_key, "size_bytes": 9, "content_hash": ""}
            },
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert (job_dir / "out.json").read_bytes() == b"ref-bytes"
    assert storage.objects["jobs/gate16-ws/gate16-job/out.json"] == b"ref-bytes"
    row = store.row_for_node("gate16-job", "node_a", "out.json")
    assert row is not None
    assert row["content_hash"] == hashlib.sha256(b"ref-bytes").hexdigest()


def test_completion_missing_job_dir_fails_result_not_commit(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """CI 回归（routes 套件抓到）：job_dir 从未存在过时，staging 目录创建
    的 FileNotFoundError 必须走失败转换——结果翻 failed 收尾 lease，而
    不是炸穿结果提交（staging 化前该异常由解包函数内的同一 except 臂
    兜住）。"""
    _seed_completion_job(job_db, workspace_id="gate17-ws", job_id="gate17-job")
    storage = FakeObjectStorage()
    handler, _store, _jobs_dir = _completion_handler(job_db, tmp_path, storage)
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"out.json": b"x"})

    assert _finish_with_archive(handler, job_id="gate17-job") is True

    assert _node_row("gate17-job", "node_a")["status"] == "failed"


def test_completion_ref_channel_survives_archive_directory_collision(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """codex #774 P2 回归：归档在 expected 名上解出目录 + 同名 dict-ref——
    预检判形状兼容（目录不产生归档落点），ref 通道三面胜出，overwrite
    遍清掉视图垃圾目录后正常链接。旧代码在视图链接 unlink 目录抛
    IsADirectoryError：remote promote 已提交、staging key 已删、lease
    卡死——现在结果照常 completed。"""
    _seed_completion_job(job_db, workspace_id="gate18-ws", job_id="gate18-job")
    storage = FakeObjectStorage()
    staging_key = "jobs-staging/gate18-ws/gate18-job/exec-1/out.json"
    storage.objects[staging_key] = b"ref-bytes"
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate18-ws" / "gate18-job"
    job_dir.mkdir(parents=True)
    # 归档只带目录成员（解包出 out.json/ 目录与垃圾文件），不产生归档落点。
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"out.json/junk.txt": b"junk"})

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="gate18-job",
        node_key="node_a",
        manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={
                "out.json": {"storage_key": staging_key, "size_bytes": 9, "content_hash": ""}
            },
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("gate18-job", "node_a")["status"] == "completed"
    assert (job_dir / "out.json").is_file()
    assert (job_dir / "out.json").read_bytes() == b"ref-bytes"
    assert storage.objects["jobs/gate18-ws/gate18-job/out.json"] == b"ref-bytes"
    # staging 源在 finish 提交后由完成方删除（窗口内绝不删，#774 对抗复审）。
    assert staging_key not in storage.objects
    assert store.row_for_node("gate18-job", "node_a", "out.json") is not None


def test_completion_prefix_clash_fails_cleanly_before_any_apply(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """预检结构性修复：归档落点 reports（文件）与 remote ref 落点
    reports/out.json 前缀相撞——任何字节移动之前判 failed：authority 零
    copy（Worker staging 对象原样保留待 lifecycle）、清单零登记、remote
    字节零落盘；参与冲突的输出 moves 不随失败 finish 落盘（codex #774
    P2——挂上会让失败结果污染 job_dir），未参与冲突的 node.log 观测
    move 照常落盘（P3 parity）。旧代码会在闸内把已 promote 的 remote
    文件随目录备份静默删掉，或在视图链接炸穿结果提交。"""
    _seed_completion_job(job_db, workspace_id="gate19-ws", job_id="gate19-job")
    storage = FakeObjectStorage()
    staging_key = "jobs-staging/gate19-ws/gate19-job/exec-1/reports/out.json"
    storage.objects[staging_key] = b"ref-bytes"
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate19-ws" / "gate19-job"
    job_dir.mkdir(parents=True)
    _result_archive(
        tmp_path / "bundles" / "result.tar.gz",
        {"reports": b"archive-bytes", "node.log": b"partial log"},
    )

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="gate19-job",
        node_key="node_a",
        manifest={
            "kind": "code",
            "log_path": "logs/jobs/gate19-job/node_a.log",
            "expected_outputs": ["reports", "reports/out.json"],
            "execution_id": "exec-1",
        },
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={
                "reports/out.json": {
                    "storage_key": staging_key,
                    "size_bytes": 9,
                    "content_hash": "",
                }
            },
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("gate19-job", "node_a")["status"] == "failed"
    assert "conflicting output paths" in _node_error("gate19-job", "node_a")
    assert storage.objects == {staging_key: b"ref-bytes"}  # 零 authority copy
    assert store.row_for_node("gate19-job", "node_a", "reports/out.json") is None
    assert not (job_dir / "reports").is_dir()  # remote 字节从未落盘
    # codex #774 P2：参与冲突的输出 move 不挂——失败结果不污染 job_dir；
    # 未参与冲突的 node.log 观测 move 照常落盘（失败节点的日志 parity）。
    assert not (job_dir / "reports").exists()
    assert (tmp_path / "logs" / "jobs" / "gate19-job" / "node_a.log").read_bytes() == b"partial log"


def test_completion_blocked_ancestor_fails_cleanly(job_db: JobQueries, tmp_path: Path) -> None:
    """job_dir 现场污染（前代次残留文件）挡住落点祖先——预检判 failed、
    现场原样保留；闸内 promote 的 mkdir 不再整批回滚上抛毒化 lease。"""
    _seed_completion_job(job_db, workspace_id="gate20-ws", job_id="gate20-job")
    storage = FakeObjectStorage()
    handler, _store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate20-ws" / "gate20-job"
    job_dir.mkdir(parents=True)
    (job_dir / "reports").write_bytes(b"previous-generation-leftover")
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"reports/out.json": b"bytes"})

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="gate20-job",
        node_key="node_a",
        manifest={"expected_outputs": ["reports/out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(status="completed", exit_code=0, output_artifacts={}),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("gate20-job", "node_a")["status"] == "failed"
    assert "blocked" in _node_error("gate20-job", "node_a")
    assert (job_dir / "reports").read_bytes() == b"previous-generation-leftover"
    assert not (job_dir / "reports").is_dir()


def test_finish_gate_promotion_failure_converts_to_failed_not_wedge(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """闸内兜底（预检无锁盖不住的残余竞态面）：staged_file_moves 提升在
    闸内失败时不再炸穿 finish 事务——guard 整体回滚后 completed 转
    failed 照常提交，lease 正常释放，节点不毒化成重试循环。"""
    _seed_completion_job(job_db, workspace_id="gate21-ws", job_id="gate21-job")
    repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
    job_dir = tmp_path / "jobs" / "gate21-ws" / "gate21-job"
    job_dir.mkdir(parents=True)
    missing_source = job_dir / ".result-staging-x" / "out.json"
    target = job_dir / "out.json"

    ok = repo.finish(
        "lease-1",
        ExecutionResult(
            status="completed",
            exit_code=0,
            staged_file_moves=((str(target), str(missing_source)),),
        ),
    )

    assert ok is True
    node = _node_row("gate21-job", "node_a")
    assert node["status"] == "failed"
    assert "failed to promote result files" in _node_error("gate21-job", "node_a")
    assert not target.exists()  # 半应用零残留（guard 回滚）
    with write_transaction(TEST_DATABASE_URL) as conn:
        lease = conn.execute(
            "select status from executor_leases where id=%s", ("lease-1",)
        ).fetchone()
    assert lease is not None
    assert lease["status"] == "released"


def test_completion_reserved_log_member_clash_fails_cleanly(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """P2-B 回归（#759 对抗复审）：kind=code 节点声明输出名 node.log（撞
    保留结果成员 CODE_RESULT_LOG_MEMBER）且 Worker 以 dict-ref 上报——
    预检判 failed、零字节应用。旧代码在 overwrite 遍把归档 node.log
    （log move 的 source）unlink 掉，闸内 FileNotFoundError 把执行成功
    的节点判 failed、重跑必复现。codex #774 P2 起失败 finish 只挂未参
    与冲突的观测 move：输出 move（job_dir/node.log）摘除不污染现场，
    保留源不再被它抢先消耗，node.log 观测 move 真正落盘。"""
    _seed_completion_job(job_db, workspace_id="gate22-ws", job_id="gate22-job")
    storage = FakeObjectStorage()
    staging_key = "jobs-staging/gate22-ws/gate22-job/exec-1/node.log"
    storage.objects[staging_key] = b"ref-bytes"
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate22-ws" / "gate22-job"
    job_dir.mkdir(parents=True)
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"node.log": b"captured stdout"})

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="gate22-job",
        node_key="node_a",
        manifest={
            "kind": "code",
            "log_path": "logs/jobs/gate22-job/node_a.log",
            "expected_outputs": ["node.log"],
            "execution_id": "exec-1",
        },
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={
                "node.log": {"storage_key": staging_key, "size_bytes": 9, "content_hash": ""}
            },
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("gate22-job", "node_a")["status"] == "failed"
    assert "reserved result member" in _node_error("gate22-job", "node_a")
    assert storage.objects == {staging_key: b"ref-bytes"}  # 零 authority copy
    assert store.row_for_node("gate22-job", "node_a", "node.log") is None
    assert not (job_dir / "node.log").exists()  # 冲突输出 move 摘除，零污染
    # 保留源不再被冲突 move 消耗：node.log 观测 move 真正落盘（旧代码两个
    # move 都挂，第一个消耗源、第二个被误当事务重放跳过，日志反而丢失）。
    assert (
        tmp_path / "logs" / "jobs" / "gate22-job" / "node_a.log"
    ).read_bytes() == b"captured stdout"


def test_finish_after_worker_loss_sweep_settles_nothing(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex #774 P1 回归：finish 锁前读到 active lease，worker-loss sweep
    随后持 job-mutation 锁删 lease 并重排队（不 bump 代次）——锁内重读让
    迟到 finish 什么也不做：不提升文件、不翻转 node_run/job_nodes（旧代
    码只复查代次，sweep 不 bump 代次，重排队现场会被盖成旧结果）。"""
    _seed_completion_job(job_db, workspace_id="gate23-ws", job_id="gate23-job")
    repo = ExecutorLeaseRepository(job_db, data_dir=tmp_path)
    job_dir = tmp_path / "jobs" / "gate23-ws" / "gate23-job"
    (job_dir / ".result-staging-x").mkdir(parents=True)
    source = job_dir / ".result-staging-x" / "out.json"
    source.write_bytes(b"stale-bytes")
    target = job_dir / "out.json"
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update job_nodes set status='running' where job_id='gate23-job' and node_key='node_a'"
        )

    real_lock = _lease_lifecycle.lock_job_mutation_and_read_generation

    def sweep_then_lock(conn: Any, job_id: str) -> int:
        # worker-loss sweep 的已提交效果（sweepers.py：删 lease + 重排队）。
        with write_transaction(TEST_DATABASE_URL) as sweep_conn:
            sweep_conn.execute("delete from executor_leases where id='lease-1'")
            sweep_conn.execute(
                "update job_nodes set status='pending', started_at=null, finished_at=null"
                " where job_id='gate23-job' and node_key='node_a'"
            )
        return real_lock(conn, job_id)

    monkeypatch.setattr(_lease_lifecycle, "lock_job_mutation_and_read_generation", sweep_then_lock)

    ok = repo.finish(
        "lease-1",
        ExecutionResult(
            status="completed", exit_code=0, staged_file_moves=((str(target), str(source)),)
        ),
    )

    assert ok is False  # 锁内重读发现 lease 已删 → 409 语义
    assert _node_row("gate23-job", "node_a")["status"] == "pending"  # 重排队现场原样
    assert not target.exists()  # 旧代次字节零落盘
    with write_transaction(TEST_DATABASE_URL) as conn:
        run = conn.execute(
            "select status from node_runs where job_id='gate23-job' and node_key='node_a'"
        ).fetchone()
    assert run is not None
    assert run["status"] == "running"  # node_run 也不被翻转


# ---------------------------------------------------------------------------
# codex #774 对抗复审 P1：并发 /result 重试与 staging 源保留
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# codex #774 对抗复审 P2：多重/兄弟冲突的全量摘除
# ---------------------------------------------------------------------------


def test_completion_disjoint_conflicts_drop_every_conflicting_move(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """两对不相交的前缀冲突（归档 a vs ref a/b、归档 x vs ref x/y）：names
    必须是全集——失败 finish 把四者全部摘除（零污染），未参与冲突的
    node.log 观测 move 照常落盘。修复前只摘除第一对，第二对干净落盘污
    染失败节点的 job_dir（或闸内炸开连带 node.log 被整体回滚）。"""
    _seed_completion_job(job_db, workspace_id="gate25-ws", job_id="gate25-job")
    storage = FakeObjectStorage()
    handler, _store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "gate25-ws" / "gate25-job"
    job_dir.mkdir(parents=True)
    # 归档只带文件成员 a / x / node.log（a/b 与 x/y 若同进 tar，解包本身
    # 就会撞 File exists——那是另一条更早的防线）；嵌套名由 ref 通道宣称。
    _result_archive(
        tmp_path / "bundles" / "result.tar.gz",
        {"a": b"a-bytes", "x": b"x-bytes", "node.log": b"partial log"},
    )

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="gate25-job",
        node_key="node_a",
        manifest={
            "kind": "code",
            "log_path": "logs/jobs/gate25-job/node_a.log",
            "expected_outputs": ["a", "a/b", "x", "x/y"],
            "execution_id": "exec-1",
        },
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={
                "a/b": {
                    "storage_key": "jobs-staging/gate25-ws/gate25-job/exec-1/a/b",
                    "size_bytes": 8,
                    "content_hash": "",
                },
                "x/y": {
                    "storage_key": "jobs-staging/gate25-ws/gate25-job/exec-1/x/y",
                    "size_bytes": 8,
                    "content_hash": "",
                },
            },
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("gate25-job", "node_a")["status"] == "failed"
    assert "conflicting output paths" in _node_error("gate25-job", "node_a")
    for rel in ("a", "a/b", "x", "x/y"):
        assert not (job_dir / rel).exists(), f"conflicting move {rel} must not land"
    assert storage.objects == {}  # 冲突在 verify 之前判死，零字节应用
    assert (tmp_path / "logs" / "jobs" / "gate25-job" / "node_a.log").read_bytes() == b"partial log"


class _VerifyFailAndPolluteStorage(FakeObjectStorage):
    """ref 核验的 HEAD 期间在 job_dir 制造挡位文件，然后报告对象缺失。

    模拟「预检无锁通过 → ref 验证期间现场被并发节点 finish 污染」的残
    余窗口（预检 docstring 自认盖不住的那条竞态）。"""

    def __init__(self, blocker: Path) -> None:
        super().__init__()
        self._blocker = blocker

    def head_object(self, storage_key: str) -> Any:
        self._blocker.write_bytes(b"leftover-from-concurrent-node")
        return None


def test_completion_remote_failure_drops_blocked_moves_and_keeps_log(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """#774 对抗复审（remote_failure 分支过滤）：ref 核验失败（staging 对
    象缺失）且现场在预检后被污染（reports 落为文件挡住 reports/out.json）
    ——失败 finish 只挂闸安全的 moves：被挡 move 不进闸（否则闸内炸开
    连带 node.log 被整体回滚丢失），ok.json 与 node.log 照常落盘，污染
    现场原样保留。"""
    _seed_completion_job(job_db, workspace_id="gate26-ws", job_id="gate26-job")
    job_dir = tmp_path / "jobs" / "gate26-ws" / "gate26-job"
    blocker = job_dir / "reports"
    storage = _VerifyFailAndPolluteStorage(blocker)
    handler, _store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    jobs_dir.joinpath("gate26-ws", "gate26-job").mkdir(parents=True)
    _result_archive(
        tmp_path / "bundles" / "result.tar.gz",
        {"ok.json": b"ok", "reports/out.json": b"bytes", "node.log": b"log bytes"},
    )

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="gate26-job",
        node_key="node_a",
        manifest={
            "kind": "code",
            "log_path": "logs/jobs/gate26-job/node_a.log",
            "expected_outputs": ["ok.json", "reports/out.json"],
            "execution_id": "exec-1",
        },
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={
                "ok.json": {
                    "storage_key": "jobs-staging/gate26-ws/gate26-job/exec-1/ok.json",
                    "size_bytes": 2,
                    "content_hash": "",
                }
            },
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("gate26-job", "node_a")["status"] == "failed"
    assert "missing" in _node_error("gate26-job", "node_a")
    assert blocker.read_bytes() == b"leftover-from-concurrent-node"  # 现场原样
    assert not blocker.is_dir()
    assert (job_dir / "ok.json").read_bytes() == b"ok"  # 闸安全的归档输出照常落盘
    assert not (job_dir / "reports" / "out.json").exists()  # 被挡 move 摘除
    assert (tmp_path / "logs" / "jobs" / "gate26-job" / "node_a.log").read_bytes() == b"log bytes"

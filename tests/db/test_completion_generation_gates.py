"""completion 层与批 finish 的代次闸交错用例（#759 复审 P1-1、对抗复审 N4）。

自 tests/db/test_generation_write_gates.py 拆出（800 行拆分线）：这里钉
Worker 结果归档「只解包到 staging、文件提升挤进 finish 代次 CAS」的
completion 层语义，以及批 finish（finish_many）的 stale events 族门。
交错手法与姊妹文件一致：直接 SQL bump 模拟已提交 reset，不用裸 sleep。
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from server.app.agent_control.completion import AgentCompletionHandler, AgentOutcome
from server.app.db.transaction import write_transaction
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

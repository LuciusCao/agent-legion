"""completion 层测试的共享种子/工具（自 test_completion_generation_gates.py 拆出）。

供 tests/db 下 completion 一族测试文件共用的 job/lease 种子、归档构造与
handler 装配；命名保持下划线前缀（沿用被拆文件的用例现场，零改动迁移）。
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Any

from server.app.agent_control.completion import AgentCompletionHandler, AgentOutcome
from server.app.db.transaction import write_transaction
from server.app.executors.leases import ExecutorLeaseRepository
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

"""codex #776 复审 P2-A：sweep 删除前的锁内复核——不误删新代次写回的文件。

升级事务提交后作业立即可被调度：重置节点的新 attempt 可能在 sweep 执行
前已写出同名新字节（完成臂已登记清单行，或在跑节点已写文件、行未登记）。
``sweep_absent_input_files`` 必须在 job-mutation 锁内复核（清单行 + 生产者
节点状态），只删仍无新代次证据的名（旧字节复活面）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.jobs import JobQueries
from server.app.services.job_workflow_upgrade_sweep import sweep_absent_input_files
from server.app.storage_paths import resolve_job_dir
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.postgres


def test_sweep_preserves_new_generation_writes(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wssweep", default_workflow_key="wfsweep")
    job = queries.create_job(
        workflow_key="wfsweep",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=["a", "b", "c"],
        workspace_id=workspace["id"],
    )
    # x.json：新 attempt 已完成（清单行重登记 + 新字节）→ 保护。
    queries.update_job_node(job["id"], "a", status="completed")
    # y.json：生产者在跑（新字节已写、清单行未登记）→ 保护。
    queries.update_job_node(job["id"], "b", status="running")
    # z.json：旧字节复活面（无清单行、生产者 pending）→ 删除。
    queries.update_job_node(job["id"], "c", status="pending")
    job_dir = resolve_job_dir(job, tmp_path / "jobs")
    job_dir.mkdir(parents=True, exist_ok=True)
    for name in ("x.json", "y.json", "z.json"):
        (job_dir / name).write_text(f"fresh-{name}", encoding="utf-8")
    with queries.connect() as conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'a', 'x.json', 'k/x.json', 1, 'hash')
            """,
            (job["id"],),
        )
    producers = {"x.json": ["a"], "y.json": ["b"], "z.json": ["c"]}

    sweep_absent_input_files(
        queries, job, tmp_path / "jobs", {"x.json", "y.json", "z.json"}, producers, job["id"]
    )

    assert (job_dir / "x.json").read_text(encoding="utf-8") == "fresh-x.json"
    assert (job_dir / "y.json").read_text(encoding="utf-8") == "fresh-y.json"
    assert not (job_dir / "z.json").exists()

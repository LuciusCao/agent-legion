"""#1239 A1：token usage capture 的顺序 checkout 契约钉子。

池 1 形态（AGENT_LEGION_DB_POOL_MAX_SIZE=1）下 lease finish 必须落
node_run_token_usage 行：旧实现在调用方持有的读连接上嵌套开写事务，
10s PoolTimeout 被 best-effort catch 吞掉、usage 行静默丢失（热路径，
每个带 node_run 的 lease 完成都走）。修复后 parse 与 persist 是顺序两
次 checkout，池 1 也能服务。
"""

from __future__ import annotations

import json
import uuid
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult, LeaseClaimRequest
from server.app.jobs import JobQueries


def _pool_one_dsn(job_db: JobQueries) -> str:
    # 独立 application_name = 独立池键，env 在池创建前生效。
    return f"{job_db.dsn_identity}&application_name=m18-a1-{uuid.uuid4().hex[:12]}"


def _write_events(run_dir: Path, events: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )


def test_finish_captures_token_usage_with_pool_size_one(job_db, tmp_path, monkeypatch):
    """池 1 下 finish 的 token capture 不丢行（嵌套 checkout 形态下本用例红：
    10s checkout timeout 后 usage 行缺失）。"""
    monkeypatch.setenv("AGENT_LEGION_DB_POOL_MAX_SIZE", "1")
    dsn = _pool_one_dsn(job_db)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    pool1_db = JobQueries(dsn, data_dir / "jobs")
    repo = ExecutorLeaseRepository(pool1_db, data_dir=data_dir)

    with pool1_db.connect() as conn:
        conn.execute("insert into workspaces(id, name) values (%s, %s)", ("ws-1", "Test"))
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id) values (%s, %s, %s, %s)",
            ("job-1", "ws-1", "question", "q-1"),
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values (%s, %s, %s)",
            ("job-1", "review_keywords", "pending"),
        )

    claimed = repo.try_claim(
        LeaseClaimRequest(
            executor_id="pi-1",
            global_capacity=10,
            workspace_id="ws-1",
            job_id="job-1",
            workflow_key="demo_workflow",
            node_key="review_keywords",
            capability="review_keywords",
            local_node_limit=None,
            lease_ttl_seconds=60,
            log_path=str(data_dir / "logs" / "run.log"),
        )
    )
    assert claimed is not None

    run_dir = data_dir / "jobs" / "ws-1" / "job-1" / "runs" / "review_keywords" / "run-token"
    _write_events(
        run_dir,
        [
            {
                "type": "message_end",
                "message": {"usage": {"input": 20, "output": 10, "cacheRead": 2}},
            }
        ],
    )
    (run_dir / "run.json").write_text(
        json.dumps(
            {"model": {"provider": "gateway", "model": "your-model-a"}, "skill_version": "v2"}
        ),
        encoding="utf-8",
    )

    result = ExecutionResult(
        status="completed",
        exit_code=0,
        command=("pi",),
        log_path="",
        run_dir=str(run_dir.relative_to(data_dir)),
    )
    assert repo.finish(claimed.lease_id, result) is True

    with closing(connect_database(dsn)) as conn, conn:
        row = conn.execute("select * from node_run_token_usage").fetchone()
    assert row is not None
    assert row["input_tokens"] == 20
    assert row["output_tokens"] == 10
    assert row["total_tokens"] == 32

"""Ready-gate hydration 端到端测试族的共享脚手架（#759 P1；codex #776 R8
拆分）。

test 模块间禁止互相 import——``test_ready_gate_hydration.py`` 按主题拆出
``test_ready_gate_hydration_upgrade.py`` 后，公共的链定义 / 清单行播种 /
下游重置 / claim 断言 / 最小 job 构造集中在这里。
"""

from __future__ import annotations

import hashlib
from contextlib import closing

from server.app.db.connection import connect_database
from server.app.db.transaction import write_transaction
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import mark_nodes_for_rerun
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL

A_PAYLOAD = b'{"from": "a"}'


def chain_definition(b_cap: str = "cap_b") -> WorkflowDefinition:
    """a → b 两级链：a 产出 a_out.json，b 声明它为输入。"""
    return WorkflowDefinition(
        key="wfchain",
        label="Wf Chain",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability=b_cap,
                after=["a"],
                config_schema={},
                inputs=["a_out.json"],
                outputs=["b_out.json"],
            ),
        },
    )


def seed_manifest_row(queries: JobQueries, job_id: str, storage_key: str, payload: bytes) -> None:
    """落一条 (a, a_out.json) 清单行，内容与 FakeObjectStorage 中的对象一致。"""
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'a', 'a_out.json', %s, %s, %s)
            """,
            (job_id, storage_key, len(payload), hashlib.sha256(payload).hexdigest()),
        )


def reset_downstream(queries: JobQueries, job_id: str) -> None:
    """下游 b 被 rerun/reset 的最小真实路径（rerun 的原子 mutation）。"""
    del queries  # 口径说明参数；mutation 走裸事务（与生产 reset 同入口）。
    with write_transaction(TEST_DATABASE_URL) as conn:
        mark_nodes_for_rerun(conn, job_id, ["b"], {"b": []})


def assert_claimed_b(worker, queries: JobQueries, job_id: str) -> None:
    """b 已 claim（本地 code 池持租约 + future 已提交）。"""
    assert worker.leases.active_counts("code").get("global", 0) == 1
    assert len(worker.state.futures) == 1
    assert queries.get_job_node(job_id, "b")["status"] == "running"


def pending_b_job(queries: JobQueries, workspace: dict) -> dict:
    """a completed、b pending 的最小 job（wfchain 定义）。"""
    job = queries.create_job(
        workflow_key="wfchain",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a", "b"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "a", status="completed")
    return job

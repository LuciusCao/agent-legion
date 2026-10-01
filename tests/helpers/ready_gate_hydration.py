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
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
)
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


# ---------------------------------------------------------------------------
# #779 列车 R4 族的恢复面收窄测试共享定义（test_ready_gate_hydration_scope.py
# 拆出单元形态族 test_ready_gate_hydration_probe_surface.py 后，单元与
# 端到端两侧共用）。纯定义构造，不触库不触文件系统。
# ---------------------------------------------------------------------------


def _conditional_branches_definition() -> WorkflowDefinition:
    """gate 产 decision.json；gate→good / gate→alt 两条条件边；b 独立分支。"""
    return WorkflowDefinition(
        key="wfcond",
        label="Wf Cond",
        intake=WorkflowIntake(),
        nodes={
            "gate": WorkflowNode(
                key="gate", label="Gate", capability="cap_gate", outputs=["decision.json"]
            ),
            "good": WorkflowNode(
                key="good", label="Good", capability="cap_good", outputs=["good_out.json"]
            ),
            "alt": WorkflowNode(
                key="alt", label="Alt", capability="cap_alt", outputs=["alt_out.json"]
            ),
            "b": WorkflowNode(key="b", label="B", capability="cap_b", outputs=["b_out.json"]),
        },
        edges=[
            WorkflowEdge(
                source="gate",
                target="good",
                condition=WorkflowCondition("decision.json", "$.eligible", True),
            ),
            WorkflowEdge(
                source="gate",
                target="alt",
                condition=WorkflowCondition("decision.json", "$.eligible", False),
            ),
        ],
    )


def _implicit_consumer_definition() -> WorkflowDefinition:
    """gate→good/alt 条件边；b 经 node.inputs 隐式消费 good 的产物（无显式
    边）——分支裁决的显式可达集不含 b。"""
    return WorkflowDefinition(
        key="wfimpl",
        label="Wf Impl",
        intake=WorkflowIntake(),
        nodes={
            "gate": WorkflowNode(
                key="gate", label="Gate", capability="cap_gate", outputs=["decision.json"]
            ),
            "good": WorkflowNode(
                key="good", label="Good", capability="cap_good", outputs=["good_out.json"]
            ),
            "alt": WorkflowNode(
                key="alt", label="Alt", capability="cap_alt", outputs=["alt_out.json"]
            ),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                inputs=["good_out.json"],
                outputs=["b_out.json"],
            ),
        },
        edges=[
            WorkflowEdge(
                source="gate",
                target="good",
                condition=WorkflowCondition("decision.json", "$.eligible", True),
            ),
            WorkflowEdge(
                source="gate",
                target="alt",
                condition=WorkflowCondition("decision.json", "$.eligible", False),
            ),
        ],
    )


def _confluence_definition() -> WorkflowDefinition:
    """gate 产 decision.json；条件边 gate→good、无条件边 gate→j 与
    good→j（汇合）。j 恒在 selected 侧，条件 verdict 不门控它。"""
    return WorkflowDefinition(
        key="wfconf",
        label="Wf Conf",
        intake=WorkflowIntake(),
        nodes={
            "gate": WorkflowNode(
                key="gate", label="Gate", capability="cap_gate", outputs=["decision.json"]
            ),
            "good": WorkflowNode(
                key="good", label="Good", capability="cap_good", outputs=["good_out.json"]
            ),
            "j": WorkflowNode(key="j", label="J", capability="cap_j", outputs=["j_out.json"]),
        },
        edges=[
            WorkflowEdge(
                source="gate",
                target="good",
                condition=WorkflowCondition("decision.json", "$.eligible", True),
            ),
            WorkflowEdge(source="gate", target="j"),
            WorkflowEdge(source="good", target="j"),
        ],
    )


def _selected_sibling_definition() -> WorkflowDefinition:
    """A 是条件边 s→a（a.json 由 s 产）；B 是条件兄弟边 s→b（b.json 由
    独立节点 p 产——翻转形态要把 B 的生产者置于在途）；b→a→j。B 选中时
    A 的可达集被 B 的 selected_reachable 覆盖。"""
    return WorkflowDefinition(
        key="wfc6",
        label="Wf C6",
        intake=WorkflowIntake(),
        nodes={
            "s": WorkflowNode(key="s", label="S", capability="cap_s", outputs=["a.json"]),
            "p": WorkflowNode(key="p", label="P", capability="cap_p", outputs=["b.json"]),
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
            "b": WorkflowNode(key="b", label="B", capability="cap_b", outputs=["b_out.json"]),
            "j": WorkflowNode(key="j", label="J", capability="cap_j", outputs=["j_out.json"]),
        },
        edges=[
            WorkflowEdge(
                source="s", target="a", condition=WorkflowCondition("a.json", "$.ok", True)
            ),
            WorkflowEdge(
                source="s", target="b", condition=WorkflowCondition("b.json", "$.ok", True)
            ),
            WorkflowEdge(source="b", target="a"),
            WorkflowEdge(source="a", target="j"),
        ],
    )

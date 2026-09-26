"""Ready-gate hydration 的查询节奏与恢复面收窄（#759 复审 P1 族）回归。

姊妹文件 test_ready_gate_hydration.py 钉 hydration 的语义（恢复、defer、
代次夹逼）；本文件钉它的**代价与范围**：

- running job 每轮绕过评估缓存重评（scan.collect_ready_candidates），
  hydration 在没有任何可恢复清单行时不得做第二次代次读——恢复写为空、
  夹逼没有保护对象，否则扫描退化为每轮每 job 三次串行 DB 查询的 N+1
  （codex 复审 P1，eval_batch / input_hydration）。
- 恢复/defer 面按当前 node statuses 收窄：已完成并被淘汰缓存的 job 做
  单分支 targeted rerun 时，其他终态分支永久丢失/损坏的对象不得挟持
  整个 job 的评估（codex 复审 P1，input_hydration 的恢复面从全定义消
  费索引键集收窄为本轮探针集）。
"""

from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.db.transaction import write_transaction
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import mark_nodes_for_rerun
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _make_worker, _seed_trivial_node_code


def _external_input_definition() -> WorkflowDefinition:
    """c（无依赖长跑）+ b（声明无生产者的外部输入 ext.json，永远等不到）。"""
    return WorkflowDefinition(
        key="wfext",
        label="Wf Ext",
        intake=WorkflowIntake(),
        nodes={
            "c": WorkflowNode(key="c", label="C", capability="cap_c", outputs=["c_out.json"]),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                inputs=["ext.json"],
                outputs=["b_out.json"],
            ),
        },
    )


def test_running_job_without_recoverable_rows_skips_generation_recheck(tmp_path: Path) -> None:
    """running job 每轮重评时 hydration 的 DB 查询定额：清单读 1 次 + 代次
    预读 1 次；无可恢复清单行（恢复写为空）时**不做**第二次代次读。

    修复前每轮每 job 串行 3 次查询（代次预读 + 清单 + 代次复核），running
    job 绕过评估缓存使该成本每轮重复——两个测量轮修复前代次读为 4 次。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfext", default_workflow_key="wfext", workspace_id="wfext"
    )
    job = queries.create_job(
        workflow_key="wfext",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["c", "b"],
        workspace_id=workspace["id"],
    )

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    manifest_reads = 0
    generation_reads = 0
    original_rows_for_job = store.rows_for_job

    def counting_rows_for_job(job_id: str):
        nonlocal manifest_reads
        manifest_reads += 1
        return original_rows_for_job(job_id)

    store.rows_for_job = counting_rows_for_job  # type: ignore[method-assign]
    # c 可 claim（长跑阻塞在 executor 上 → job 持 running 状态）。
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfext", "c")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [_external_input_definition()],
        artifact_object_store=store,
    )
    original_get_generation = worker.job_db.get_job_execution_generation

    def counting_get_generation(job_id: str):
        nonlocal generation_reads
        generation_reads += 1
        return original_get_generation(job_id)

    worker.job_db.get_job_execution_generation = counting_get_generation  # type: ignore[method-assign]

    worker._poll()
    assert queries.get_job_node(job["id"], "c")["status"] == "running"
    worker._poll()  # claim bump 了 mark：本轮重评后 job 进入 running 缓存绕过态

    manifest_reads = 0
    generation_reads = 0
    worker._poll()
    worker._poll()

    # 两个测量轮：ext.json 本地缺失且无清单行（真缺失），恢复写恒为空——
    # 每轮清单读 1 次 + 代次预读 1 次，无第二次代次读。
    assert manifest_reads == 2
    assert generation_reads == 2

    executor.block_event.set()
    worker.stop()


def _two_branch_definition() -> WorkflowDefinition:
    """a1 → a2（a2 消费 a1_out.json）与独立分支 b：两条互不相干的支路。"""
    return WorkflowDefinition(
        key="wf2br",
        label="Wf Two Branch",
        intake=WorkflowIntake(),
        nodes={
            "a1": WorkflowNode(key="a1", label="A1", capability="cap_a1", outputs=["a1_out.json"]),
            "a2": WorkflowNode(
                key="a2",
                label="A2",
                capability="cap_a2",
                after=["a1"],
                inputs=["a1_out.json"],
                outputs=["a2_out.json"],
            ),
            "b": WorkflowNode(key="b", label="B", capability="cap_b", outputs=["b_out.json"]),
        },
    )


def test_terminal_branch_lost_object_does_not_block_targeted_rerun(tmp_path: Path) -> None:
    """已完成并被淘汰缓存的 job 单分支 targeted rerun：另一终态分支的
    manifest-only 产物对象永久丢失也不得挟持本轮评估。

    修复前恢复面是整个定义的消费索引键集：a1_out.json（消费者 a2 已
    completed）恢复失败 → 非空 unrestored 跳过整个 job 的评估缓存，b 的
    rerun 永远到不了 claim。收窄后 a1_out.json 不在本轮探针集（其唯一消
    费者终态），b 照常评估并被 claim。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wf2br", default_workflow_key="wf2br", workspace_id="wf2br"
    )
    job = queries.create_job(
        workflow_key="wf2br",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a1", "a2", "b"],
        workspace_id=workspace["id"],
    )
    for key in ("a1", "a2", "b"):
        queries.update_job_node(job["id"], key, status="completed")
    queries.update_job_status(job["id"], "completed")
    # 终态分支的清单行在、本地文件被淘汰、对象永久丢失（FakeObjectStorage
    # 刻意为空）——该对象本轮没有任何可运行/可评估的消费者。
    payload = b'{"from": "a1"}'
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'a1', 'a1_out.json', %s, %s, %s)
            """,
            (
                job["id"],
                f"jobs/{workspace['id']}/{job['id']}/a1_out.json",
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
        )

    # targeted rerun b（真实原子 mutation：b 回 pending、bump 代次）。
    with write_transaction(TEST_DATABASE_URL) as conn:
        mark_nodes_for_rerun(conn, job["id"], ["b"], {"b": []})

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wf2br", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [_two_branch_definition()],
        artifact_object_store=store,
    )

    worker._poll()

    # 无关终态分支的丢失对象不进入 defer 集：b 的 rerun 越过评估直接被
    # claim（本地 code 池持租约 + future 已提交）。
    assert queries.get_job_node(job["id"], "b")["status"] == "running"
    assert worker.leases.active_counts("code").get("global", 0) == 1
    assert queries.get_job_node(job["id"], "a2")["status"] == "completed"  # 终态分支原样

    executor.block_event.set()
    worker.stop()


# ---------------------------------------------------------------------------
# #779 列车 R4 复审 P1：终态分支的条件产物退出恢复面
# ---------------------------------------------------------------------------


def _conditional_branches_definition() -> WorkflowDefinition:
    """gate 产 decision.json；gate→good / gate→alt 两条条件边；b 独立分支。"""
    from server.app.workflows.schema import WorkflowCondition, WorkflowEdge

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


def test_condition_artifact_of_fully_decided_branch_leaves_probe_surface() -> None:
    """#779 R4 P1：source completed 臂的过度包含——终态分支（target 已终态、
    其可达节点也全终态）的条件产物不再影响任何可运行分支，本地缓存被淘汰
    且对象丢失时不得进恢复面（否则恢复失败把整个 job 卡在 defer）。"""
    from server.app.workflow_worker.input_hydration import live_probe_names

    definition = _conditional_branches_definition()
    statuses = {"gate": "completed", "good": "completed", "alt": "not_applicable", "b": "pending"}

    assert "decision.json" not in live_probe_names(definition, statuses)


def test_condition_artifact_stays_while_verdict_still_drives_runnable_nodes() -> None:
    """对照（当初 source-completed 臂要保的裁决稳定性）：target 已完成但
    其下游仍可运行时，条件文件在场与否仍决定 not_applicable 标记——名字
    必须留在恢复面；source completed + target pending（尚未裁决）同理。"""
    from server.app.workflow_worker.input_hydration import live_probe_names

    definition = _conditional_branches_definition()
    # target pending（未裁决）：条件文件必须可评估。
    pending_target = {"gate": "completed", "good": "pending", "alt": "pending", "b": "completed"}
    assert "decision.json" in live_probe_names(definition, pending_target)
    # target 已 completed（已选中），但同分支仍有 pending 节点时 verdict 必须
    # 稳定——给 good 接一个下游节点覆盖该形态。
    from server.app.workflows.schema import WorkflowEdge as _Edge
    from server.app.workflows.schema import WorkflowNode as _Node

    with_downstream = WorkflowDefinition(
        key="wfcond",
        label="Wf Cond",
        intake=WorkflowIntake(),
        nodes={
            **definition.nodes,
            "good_down": _Node(
                key="good_down",
                label="GoodDown",
                capability="cap_good_down",
                outputs=["gd_out.json"],
            ),
        },
        edges=[*definition.edges, _Edge(source="good", target="good_down")],
    )
    downstream_pending = {
        "gate": "completed",
        "good": "completed",
        "alt": "not_applicable",
        "good_down": "pending",
        "b": "completed",
    }
    assert "decision.json" in live_probe_names(with_downstream, downstream_pending)


def test_decided_branch_lost_condition_object_does_not_block_targeted_rerun(
    tmp_path: Path,
) -> None:
    """#779 R4 P1 的端到端形态：已裁决分支的本地缓存被淘汰、清单行仍在但
    对象永久丢失，无关分支 targeted rerun 时 hydration 不得把该条件产物放
    进恢复面——恢复失败曾使整个 job 跳过评估，无关分支永远到不了 claim。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfcond", default_workflow_key="wfcond", workspace_id="wfcond"
    )
    job = queries.create_job(
        workflow_key="wfcond",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["gate", "good", "alt", "b"],
        workspace_id=workspace["id"],
    )
    # 分支已裁决完毕：good 选中并 completed，alt not_applicable。
    queries.update_job_node(job["id"], "gate", status="completed")
    queries.update_job_node(job["id"], "good", status="completed")
    queries.update_job_node(job["id"], "alt", status="not_applicable")
    queries.update_job_node(job["id"], "b", status="completed")
    queries.update_job_status(job["id"], "completed")
    # 条件产物的清单行在、本地文件被淘汰、对象永久丢失（FakeObjectStorage
    # 刻意为空）。
    payload = b'{"eligible": true}'
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'gate', 'decision.json', %s, %s, %s)
            """,
            (
                job["id"],
                f"jobs/{workspace['id']}/{job['id']}/decision.json",
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
        )

    # targeted rerun b（真实原子 mutation：b 回 pending、bump 代次）。
    with write_transaction(TEST_DATABASE_URL) as conn:
        mark_nodes_for_rerun(conn, job["id"], ["b"], {"b": []})

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfcond", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [_conditional_branches_definition()],
        artifact_object_store=store,
    )

    worker._poll()

    # 已裁决分支的丢失条件对象不进入 defer 集：b 的 rerun 越过评估直接被
    # claim；已裁决分支原样。
    assert queries.get_job_node(job["id"], "b")["status"] == "running"
    assert worker.leases.active_counts("code").get("global", 0) == 1
    assert queries.get_job_node(job["id"], "good")["status"] == "completed"
    assert queries.get_job_node(job["id"], "alt")["status"] == "not_applicable"

    executor.block_event.set()
    worker.stop()


# ---------------------------------------------------------------------------
# #779 列车 R4 复审 P1 跟进：恢复面的可达口径与分支裁决一致（显式边）
# ---------------------------------------------------------------------------


def _implicit_consumer_definition() -> WorkflowDefinition:
    """gate→good/alt 条件边；b 经 node.inputs 隐式消费 good 的产物（无显式
    边）——分支裁决的显式可达集不含 b。"""
    from server.app.workflows.schema import WorkflowCondition, WorkflowEdge

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


def test_condition_artifact_excluded_when_only_implicit_consumer_runnable() -> None:
    """#779 R4 P1 跟进：条件 verdict 的传播口径是 evaluate_branches 的显式
    边可达集（_reachable_from）。target 已终态、只有隐式消费边（node.
    inputs）可达的节点可运行时，条件 verdict 根本不影响该隐式消费者——
    合并闭包（显式 ∪ 隐式）会把它错算成「verdict 仍在驱动」，让已淘汰且
    对象丢失的条件文件每轮恢复失败、把无关 rerun 卡死在 defer。"""
    from server.app.workflow_worker.input_hydration import live_probe_names

    definition = _implicit_consumer_definition()
    statuses = {"gate": "completed", "good": "completed", "alt": "not_applicable", "b": "pending"}

    # b 的隐式 input 仍在恢复面（b 可运行），但已裁决分支的条件产物退出。
    names = live_probe_names(definition, statuses)
    assert "good_out.json" in names
    assert "decision.json" not in names


def test_implicit_consumer_rerun_not_blocked_by_lost_condition_object(tmp_path: Path) -> None:
    """端到端：completed 的 good 的产物被无显式边的 pending b 隐式消费，
    b 被 targeted rerun 且条件文件对象永久丢失——hydration 不得把
    decision.json 放进恢复面，b 照常 claim。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfimpl", default_workflow_key="wfimpl", workspace_id="wfimpl"
    )
    job = queries.create_job(
        workflow_key="wfimpl",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["gate", "good", "alt", "b"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "gate", status="completed")
    queries.update_job_node(job["id"], "good", status="completed")
    queries.update_job_node(job["id"], "alt", status="not_applicable")
    queries.update_job_node(job["id"], "b", status="completed")
    queries.update_job_status(job["id"], "completed")
    # 条件产物清单行在、本地被淘汰、对象永久丢失（FakeObjectStorage 刻意
    # 为空）；b 的隐式 input（good_out.json）由已完成的 good 产出、本地
    # 仍在（生产者终态，文件未淘汰），不就绪不能赖它。
    payload = b'{"eligible": true}'
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'gate', 'decision.json', %s, %s, %s)
            """,
            (
                job["id"],
                f"jobs/{workspace['id']}/{job['id']}/decision.json",
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
        )

    with write_transaction(TEST_DATABASE_URL) as conn:
        mark_nodes_for_rerun(conn, job["id"], ["b"], {"b": []})

    # b 的隐式 input 在本地 job_dir（已完成生产者的产出，未被淘汰）。
    from server.app.jobs.storage_layout import job_storage_dir

    job_dir = job_storage_dir(tmp_path / "jobs", workspace["id"], job["id"])
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "good_out.json").write_text('{"from": "good"}', encoding="utf-8")

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfimpl", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [_implicit_consumer_definition()],
        artifact_object_store=store,
    )

    worker._poll()

    # b 越过评估直接被 claim；已裁决分支原样。
    assert queries.get_job_node(job["id"], "b")["status"] == "running"
    assert worker.leases.active_counts("code").get("global", 0) == 1
    assert queries.get_job_node(job["id"], "good")["status"] == "completed"

    executor.block_event.set()
    worker.stop()


# ---------------------------------------------------------------------------
# #779 列车 R4 复审 P1 跟进②：汇合形态——无条件兄弟边可达的节点不受条件
# verdict 门控（裁决差集 unselected_reachable - selected_reachable）
# ---------------------------------------------------------------------------


def _confluence_definition() -> WorkflowDefinition:
    """gate 产 decision.json；条件边 gate→good、无条件边 gate→j 与
    good→j（汇合）。j 恒在 selected 侧，条件 verdict 不门控它。"""
    from server.app.workflows.schema import WorkflowCondition, WorkflowEdge

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


def test_confluence_via_unconditional_sibling_excludes_condition_artifact() -> None:
    """#779 R4 P1 跟进②：条件边 s→a（a 终态）+ 无条件边 s→j + a→j 汇合，
    j 被 targeted rerun——j 经无条件边恒可达（恒在 selected 侧），条件
    verdict 的差集（unselected_reachable - selected_reachable）不覆盖它；
    条件产物已淘汰且对象丢失不得因此进恢复面阻塞 j。"""
    from server.app.workflow_worker.input_hydration import live_probe_names

    definition = _confluence_definition()
    statuses = {"gate": "completed", "good": "completed", "j": "pending"}

    assert "decision.json" not in live_probe_names(definition, statuses)
    # 对照：没有无条件兄弟边时（a→j 是唯一路径），j 的可运行性受
    # verdict 门控——decision.json 必须留在恢复面。
    edges_without_sibling = [
        edge for edge in definition.edges if not (edge.source == "gate" and edge.target == "j")
    ]
    from dataclasses import replace as _replace

    gated_only = _replace(definition, edges=edges_without_sibling)
    assert "decision.json" in live_probe_names(gated_only, statuses)


def test_confluence_rerun_not_blocked_by_lost_condition_object(tmp_path: Path) -> None:
    """端到端（汇合形态）：good 终态、j 经无条件边被 targeted rerun、
    条件对象永久丢失——hydration 不恢复 decision.json，j 照常 claim。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfconf", default_workflow_key="wfconf", workspace_id="wfconf"
    )
    job = queries.create_job(
        workflow_key="wfconf",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["gate", "good", "j"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "gate", status="completed")
    queries.update_job_node(job["id"], "good", status="completed")
    queries.update_job_node(job["id"], "j", status="completed")
    queries.update_job_status(job["id"], "completed")
    # 条件产物清单行在、本地被淘汰、对象永久丢失（FakeObjectStorage 为空）。
    payload = b'{"eligible": true}'
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'gate', 'decision.json', %s, %s, %s)
            """,
            (
                job["id"],
                f"jobs/{workspace['id']}/{job['id']}/decision.json",
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
        )

    with write_transaction(TEST_DATABASE_URL) as conn:
        mark_nodes_for_rerun(conn, job["id"], ["j"], {"j": []})

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfconf", "j")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [_confluence_definition()],
        artifact_object_store=store,
    )

    worker._poll()

    assert queries.get_job_node(job["id"], "j")["status"] == "running"
    assert worker.leases.active_counts("code").get("global", 0) == 1
    assert queries.get_job_node(job["id"], "good")["status"] == "completed"

    executor.block_event.set()
    worker.stop()


# ---------------------------------------------------------------------------
# #779 列车 R4 复审 P1 跟进③（结构性）：条件产物的唯一入口是裁决差集筛选，
# 不得经普通 input 入口（消费索引把条件边 target 记作消费者）先行合入
# ---------------------------------------------------------------------------


def test_conditional_target_with_unconditional_path_not_in_probe_surface() -> None:
    """codex 本轮形态：s completed；条件边 s→j（decision.json）与无条件
    路径 s→u→j 汇合于 pending 的 j（targeted rerun）。j 恒经无条件路径
    可达（selected 侧），条件 verdict 不影响 j——但消费索引把 j 记作
    decision.json 的消费者，普通 input 入口（消费者可运行）会先行合入、
    差集筛选只增不减——结构性修复后 decision.json 不进恢复面。"""
    from server.app.workflow_worker.input_hydration import live_probe_names
    from server.app.workflows.schema import WorkflowCondition, WorkflowEdge

    definition = WorkflowDefinition(
        key="wfc3",
        label="Wf C3",
        intake=WorkflowIntake(),
        nodes={
            "s": WorkflowNode(key="s", label="S", capability="cap_s", outputs=["decision.json"]),
            "u": WorkflowNode(key="u", label="U", capability="cap_u", outputs=["u_out.json"]),
            "j": WorkflowNode(key="j", label="J", capability="cap_j", outputs=["j_out.json"]),
        },
        edges=[
            WorkflowEdge(
                source="s",
                target="j",
                condition=WorkflowCondition("decision.json", "$.eligible", True),
            ),
            WorkflowEdge(source="s", target="u"),
            WorkflowEdge(source="u", target="j"),
        ],
    )
    statuses = {"s": "completed", "u": "completed", "j": "pending"}

    assert "decision.json" not in live_probe_names(definition, statuses)


def test_condition_artifact_shared_with_plain_input_follows_input_channel() -> None:
    """对抗自查形态 (a)：条件产物名同时被普通 node.inputs 声明——input
    渠道的消费者可运行时（find_ready_nodes 真实探它），名字经 input 入口
    照常进恢复面；该消费者也终态且裁决差集为空时才退出。"""
    from server.app.workflow_worker.input_hydration import live_probe_names
    from server.app.workflows.schema import WorkflowCondition, WorkflowEdge

    definition = WorkflowDefinition(
        key="wfc4",
        label="Wf C4",
        intake=WorkflowIntake(),
        nodes={
            "s": WorkflowNode(key="s", label="S", capability="cap_s", outputs=["decision.json"]),
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                inputs=["decision.json"],
                outputs=["b_out.json"],
            ),
        },
        edges=[
            WorkflowEdge(
                source="s",
                target="a",
                condition=WorkflowCondition("decision.json", "$.eligible", True),
            ),
        ],
    )
    # b 可运行：b 真实把 decision.json 当 input 探——必须进恢复面。
    runnable_consumer = {"s": "completed", "a": "completed", "b": "pending"}
    assert "decision.json" in live_probe_names(definition, runnable_consumer)
    # b 也终态、a 终态（差集为空）：退出。
    all_terminal = {"s": "completed", "a": "completed", "b": "completed"}
    assert "decision.json" not in live_probe_names(definition, all_terminal)


def test_condition_artifact_multilayer_confluence() -> None:
    """对抗自查形态 (b)：多层汇合——条件 target a 的显式下游 x 又被无条件
    路径（s→u→x）汇合。x 恒可达（selected 侧）时条件 verdict 不门控它；
    a 已终态则 decision.json 退出恢复面。a 的下游中还有无条件路径覆盖不
    到的可运行节点 y 时，verdict 仍门控 y——必须留在恢复面。"""
    from server.app.workflow_worker.input_hydration import live_probe_names
    from server.app.workflows.schema import WorkflowCondition, WorkflowEdge

    base_nodes = {
        "s": WorkflowNode(key="s", label="S", capability="cap_s", outputs=["decision.json"]),
        "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
        "u": WorkflowNode(key="u", label="U", capability="cap_u", outputs=["u_out.json"]),
        "x": WorkflowNode(key="x", label="X", capability="cap_x", outputs=["x_out.json"]),
    }
    base_edges = [
        WorkflowEdge(
            source="s",
            target="a",
            condition=WorkflowCondition("decision.json", "$.eligible", True),
        ),
        WorkflowEdge(source="a", target="x"),
        WorkflowEdge(source="s", target="u"),
        WorkflowEdge(source="u", target="x"),
    ]
    definition = WorkflowDefinition(
        key="wfc5", label="Wf C5", intake=WorkflowIntake(), nodes=base_nodes, edges=base_edges
    )
    # a 终态、x 可运行但恒经无条件路径可达 → 退出。
    statuses = {"s": "completed", "a": "completed", "u": "completed", "x": "pending"}
    assert "decision.json" not in live_probe_names(definition, statuses)

    # a 的下游 y 不被无条件路径覆盖且可运行 → verdict 仍门控 y → 留在恢复面。
    nodes_with_y = {
        **base_nodes,
        "y": WorkflowNode(key="y", label="Y", capability="cap_y", outputs=["y_out.json"]),
    }
    with_y = WorkflowDefinition(
        key="wfc5",
        label="Wf C5",
        intake=WorkflowIntake(),
        nodes=nodes_with_y,
        edges=[*base_edges, WorkflowEdge(source="a", target="y")],
    )
    statuses_y = {**statuses, "y": "pending"}
    assert "decision.json" in live_probe_names(with_y, statuses_y)


def test_conditional_target_with_unconditional_path_rerun_not_blocked(tmp_path: Path) -> None:
    """端到端（codex 本轮形态）：条件边 s→j + 无条件路径 s→u→j，j 被
    targeted rerun，条件对象永久丢失——j 恒经无条件路径可放行
    （find_ready_nodes 经 u→j），decision.json 的丢失不得阻止 j claim。"""
    from server.app.workflows.schema import WorkflowCondition, WorkflowEdge

    definition = WorkflowDefinition(
        key="wfc3",
        label="Wf C3",
        intake=WorkflowIntake(),
        nodes={
            "s": WorkflowNode(key="s", label="S", capability="cap_s", outputs=["decision.json"]),
            "u": WorkflowNode(key="u", label="U", capability="cap_u", outputs=["u_out.json"]),
            "j": WorkflowNode(key="j", label="J", capability="cap_j", outputs=["j_out.json"]),
        },
        edges=[
            WorkflowEdge(
                source="s",
                target="j",
                condition=WorkflowCondition("decision.json", "$.eligible", True),
            ),
            WorkflowEdge(source="s", target="u"),
            WorkflowEdge(source="u", target="j"),
        ],
    )
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wfc3", default_workflow_key="wfc3", workspace_id="wfc3")
    job = queries.create_job(
        workflow_key="wfc3",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["s", "u", "j"],
        workspace_id=workspace["id"],
    )
    for key in ("s", "u", "j"):
        queries.update_job_node(job["id"], key, status="completed")
    queries.update_job_status(job["id"], "completed")
    # 条件产物清单行在、本地被淘汰、对象永久丢失（FakeObjectStorage 为空）。
    payload = b'{"eligible": true}'
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 's', 'decision.json', %s, %s, %s)
            """,
            (
                job["id"],
                f"jobs/{workspace['id']}/{job['id']}/decision.json",
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
        )

    with write_transaction(TEST_DATABASE_URL) as conn:
        mark_nodes_for_rerun(conn, job["id"], ["j"], {"j": []})

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfc3", "j")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [definition],
        artifact_object_store=store,
    )

    worker._poll()

    assert queries.get_job_node(job["id"], "j")["status"] == "running"
    assert worker.leases.active_counts("code").get("global", 0) == 1

    executor.block_event.set()
    worker.stop()

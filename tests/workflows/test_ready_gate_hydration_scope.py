"""Ready-gate hydration 的端到端 claim 联动与查询节奏回归（#759 复审 P1 族）。

姊妹文件 test_ready_gate_hydration.py 钉 hydration 的语义（恢复、defer、
代次夹逼）；恢复面收窄的单元形态族（live_probe_names 逐形态判定）在
test_ready_gate_hydration_probe_surface.py（#779 列车 R4 复审 P1——本
文件超 800 拆分线后按主题拆开，用例零改动迁移）；两侧共享的定义构造在
tests/helpers/ready_gate_hydration.py。本文件钉**代价与端到端联动**：

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
from tests.helpers.ready_gate_hydration import (
    _conditional_branches_definition,
    _confluence_definition,
    _implicit_consumer_definition,
    _selected_sibling_definition,
)
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
# #779 列车 R4 复审 P1：终态分支的条件产物退出恢复面（端到端）
# ---------------------------------------------------------------------------


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
# #779 列车 R4 复审 P1 跟进：恢复面的可达口径与分支裁决一致（端到端）
# ---------------------------------------------------------------------------


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
# #779 列车 R4 复审 P1 跟进②：汇合形态（端到端）
# ---------------------------------------------------------------------------


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
# #779 列车 R4 复审 P1 跟进③（结构性）：条件产物唯一入口（端到端）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# #779 列车 R4 复审 P1 跟进④：选中条件兄弟边进裁决差集（端到端）
# ---------------------------------------------------------------------------


def test_selected_sibling_covering_rerun_not_blocked_by_lost_object(tmp_path: Path) -> None:
    """端到端（codex 本轮形态）：b.json 本地在场（B 选中 s→b→a→j）、a.json
    清单行在但对象永久丢失、j 被 targeted rerun——a.json 不进恢复面，j
    照常 claim。"""
    definition = _selected_sibling_definition()
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wfc6", default_workflow_key="wfc6", workspace_id="wfc6")
    job = queries.create_job(
        workflow_key="wfc6",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["s", "p", "a", "b", "j"],
        workspace_id=workspace["id"],
    )
    for key in ("s", "p", "a", "b", "j"):
        queries.update_job_node(job["id"], key, status="completed")
    queries.update_job_status(job["id"], "completed")
    # a.json 清单行在、本地被淘汰、对象永久丢失（FakeObjectStorage 为空）；
    # b.json 由已完成生产者产出、本地仍在（B 当前选中）。
    payload = b'{"ok": true}'
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 's', 'a.json', %s, %s, %s)
            """,
            (
                job["id"],
                f"jobs/{workspace['id']}/{job['id']}/a.json",
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
        )
    from server.app.jobs.storage_layout import job_storage_dir

    job_dir = job_storage_dir(tmp_path / "jobs", workspace["id"], job["id"])
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "b.json").write_text('{"ok": true}', encoding="utf-8")

    with write_transaction(TEST_DATABASE_URL) as conn:
        mark_nodes_for_rerun(conn, job["id"], ["j"], {"j": []})

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfc6", "j")
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

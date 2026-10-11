"""Schema v61: workspace_workflow_drafts roundtrip (Studio YAML draft store)."""

from __future__ import annotations

import time

import pytest

from server.app.jobs.queries.workflow_drafts import DraftConflictError
from tests.postgres_support import TEST_DATABASE_URL


def test_upsert_then_get_roundtrip(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft")

    stored = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")

    assert stored["definition_yaml"] == "key: wf\n"
    assert stored["created_at"]
    assert stored["updated_at"]
    loaded = job_db.get_workspace_workflow_draft(workspace["id"])
    assert loaded is not None
    assert loaded["definition_yaml"] == "key: wf\n"


def test_get_returns_none_without_a_draft(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft-empty")

    assert job_db.get_workspace_workflow_draft(workspace["id"]) is None


def test_draft_state_read_returns_content_and_generation_in_one_snapshot(job_db) -> None:
    """#1226 六轮 codex P1：草稿内容 + 代次的原子读（单查询同一快照）——
    空态也返回一行（内容字段全 null、代次为 workspace 当前值），行在时
    与草稿行/代次行同源。"""
    workspace = job_db.create_workspace("ws-draft-state")

    empty = job_db.get_workspace_workflow_draft_state(workspace["id"])
    assert empty["definition_yaml"] is None
    assert empty["updated_at"] is None
    assert empty["generation"] == 0

    stored = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")
    state = job_db.get_workspace_workflow_draft_state(workspace["id"])
    assert state["definition_yaml"] == "key: wf\n"
    assert state["updated_at"] == stored["updated_at"]
    assert state["generation"] == stored["generation"] == 1

    # 发布删行后：内容回 null，代次仍是推进后的当前值（同一快照）。
    with job_db.connect() as conn:
        job_db.delete_workspace_workflow_draft(conn, workspace["id"])
    post_publish = job_db.get_workspace_workflow_draft_state(workspace["id"])
    assert post_publish["definition_yaml"] is None
    assert post_publish["generation"] == 2


def test_rejected_conditional_bump_leaves_no_generation_row(job_db) -> None:
    """#1226 对抗复验 P3：base>0 而代次行本就不存在的必败 CAS 写入不再兜底
    插 generation=0 行——被拒写入不留残余（base=0 的首存仍需兜底行做并发
    首存的锁目标，不受此限）。"""
    workspace = job_db.create_workspace("ws-draft-no-residue")

    with pytest.raises(DraftConflictError):
        job_db.upsert_workspace_workflow_draft_if_unchanged(workspace["id"], "key: wf\n", 5)

    with job_db.connect() as conn:
        row = conn.execute(
            "select 1 from workspace_workflow_draft_generation where workspace_id=%s",
            (workspace["id"],),
        ).fetchone()
    assert row is None


def test_cas_and_legacy_writes_on_the_empty_state_never_deadlock(job_db, monkeypatch) -> None:
    """#1226 codex 复审 P2：空草稿（行不存在）上 CAS 与 legacy LWW 并发——
    旧形态下 CAS 的 FOR UPDATE 探针锁不到行，退化为「先代次行后插草稿」，
    与 legacy「先插草稿后代次行」倒置成 AB-BA 死锁（40P01 冒成未处理的
    500）。统一协议：两条写路径先取同一 workspace 级 advisory xact 锁——
    本测试用屏障注入钉住最坏交错，断言无 40P01、双方合理收尾（CAS 成功
    或 DraftConflictError）、终态等于某种合法串行序。

    交错注入（比照 test_status_counts_deadlock 的手法）：补丁两条路径的
    代次推进函数。A（legacy，主线程）插完草稿行后等 B 的信号；B（CAS 线程）
    拿到代次行锁后举旗再等 A——旧序下环闭合，PG 死锁检测器必杀一边
    （deadlock_timeout 默认 1s）；统一锁序后 B 卡在 advisory 锁上（经
    pg_blocking_pids 观测，非裸 sleep），A 直接推进。"""
    import threading
    import time

    import psycopg

    from server.app.jobs.queries import workflow_draft_cas as cas_queries
    from server.app.jobs.queries import workflow_drafts as draft_queries
    from tests.helpers.pg_waits import backend_pid
    from tests.postgres_support import TEST_SCHEMA

    workspace = job_db.create_workspace(f"ws-draft-lock-order-{TEST_SCHEMA}")
    workspace_id = str(workspace["id"])
    a_holds_draft = threading.Event()
    b_holds_generation = threading.Event()
    real_bump = draft_queries.bump_workflow_draft_generation
    real_bump_if_current = cas_queries.bump_workflow_draft_generation_if_current
    errors: dict[str, BaseException] = {}

    def _blocked_by(pid: int) -> bool:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as monitor:
            row = monitor.execute(
                "select exists(select 1 from pg_stat_activity"
                " where %s = any(pg_blocking_pids(pid)))",
                (pid,),
            ).fetchone()
        return bool(row[0])

    def a_bump(conn, ws):
        if ws != workspace_id:
            return real_bump(conn, ws)
        a_pid = backend_pid(conn)
        a_holds_draft.set()
        # 等信号非等时长：B 持代次锁（旧序交错）或 B 已被 A 的 advisory 锁
        # 拦住（统一锁序——B 永远到不了代次锁，直接推进）；deadline 只是护栏。
        deadline = time.monotonic() + 10
        while not b_holds_generation.is_set():
            if _blocked_by(a_pid):
                break
            assert time.monotonic() < deadline, "B 既未持代次锁也未被 A 阻塞"
            time.sleep(0.02)
        return real_bump(conn, ws)

    def b_bump_if_current(conn, ws, base):
        result = real_bump_if_current(conn, ws, base)
        if ws != workspace_id:
            return result
        b_holds_generation.set()
        # 旧序下 A 已持草稿行（事件早已竖起，零等待）；统一锁序下 B 只在 A
        # 提交后才走到这里（事件同样早已竖起）——两侧都不引入睡眠。
        a_holds_draft.wait(timeout=10)
        return result

    def run_cas() -> None:
        try:
            job_db.upsert_workspace_workflow_draft_if_unchanged(workspace_id, "key: cas\n", 0)
        except BaseException as exc:  # 测试线程内收集，主线程统一断言
            errors["cas"] = exc

    monkeypatch.setattr(draft_queries, "bump_workflow_draft_generation", a_bump)
    monkeypatch.setattr(cas_queries, "bump_workflow_draft_generation_if_current", b_bump_if_current)

    thread_b = threading.Thread(target=run_cas)
    thread_b.start()
    try:
        job_db.upsert_workspace_workflow_draft(workspace_id, "key: legacy\n")
    except BaseException as exc:  # 主线程收集，统一断言
        errors["legacy"] = exc
    thread_b.join(timeout=30)
    # B 挂死超过 join 上限必须响亮失败（否则其 errors 为空会读成假绿）。
    assert not thread_b.is_alive(), "CAS thread never resolved"

    deadlocked = [
        name for name, exc in errors.items() if isinstance(exc, psycopg.errors.DeadlockDetected)
    ]
    assert not deadlocked, f"draft write paths deadlocked: {deadlocked} ({errors})"
    assert "legacy" not in errors  # legacy LWW 在任一合法串行序下都成功
    cas_error = errors.get("cas")
    assert cas_error is None or isinstance(cas_error, DraftConflictError)
    # 终态 = 某种合法串行序：草稿行存在（唯一写入者或后到者覆盖），代次 ≥
    # 1 且与实际落库写入次数一致。
    state = job_db.get_workspace_workflow_draft_state(workspace_id)
    assert state["definition_yaml"] is not None
    assert int(state["generation"]) >= 1


def test_upsert_is_idempotent_and_advances_updated_at(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft-upsert")

    first = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")
    again = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")
    assert again["created_at"] == first["created_at"]

    # 保留的墙钟等待：断言 updated_at 严格前移，需要真实时钟推进。
    time.sleep(0.02)
    updated = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf2\n")
    assert updated["definition_yaml"] == "key: wf2\n"
    # created_at survives the update; updated_at moves forward.
    assert updated["created_at"] == first["created_at"]
    assert updated["updated_at"] > first["updated_at"]

    loaded = job_db.get_workspace_workflow_draft(workspace["id"])
    assert loaded is not None
    assert loaded["definition_yaml"] == "key: wf2\n"


def test_workspace_delete_cascades_to_the_draft(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft-cascade")
    job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")

    job_db.delete_workspace(workspace["id"])

    assert job_db.get_workspace_workflow_draft(workspace["id"]) is None


def test_workspace_delete_cancels_queued_requests(job_db) -> None:
    """#759：workspace 删除后其 queued 请求行随之物理消失（on delete
    cascade）——钉住升级路径修复依赖的 cascade 契约。"""
    workspace = job_db.create_workspace("ws-cancel-cascade")
    batch = job_db.create_run("wf", "batch_by_ids", {"ids": ["1"]}, workspace["id"])
    job = job_db.create_job(
        workflow_key="wf",
        source_type="question",
        source_id="1",
        run_id=batch["id"],
        title="j1",
        node_keys=["n1"],
        workspace_id=workspace["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, node_key,"
            " agent_id, agent_definition_hash, node_concurrency_limit,"
            " state, queued_at, manifest_json)"
            " values ('exec-ws-queued', %s, %s, 'n1',"
            " 'generator-v1', 'sha256:whatever', 1, 'queued', current_timestamp, '{}')",
            (workspace["id"], job["id"]),
        )

    job_db.delete_workspace(workspace["id"])

    with job_db.connect() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id='exec-ws-queued'"
        ).fetchone()
    assert row is None

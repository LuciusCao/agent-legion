"""#467 分块提交的并发 / 锁交错测试（#955 自 test_run_service_chunking.py 按主题拆出，用例零改动）。

- shared-id concurrency: two concurrent runs over overlapping material sets
  never duplicate a shared job row (deterministic job id + ON CONFLICT);
  FOR KEY SHARE locks are mutually compatible, so the lock phase itself has
  no cross-run deadlock surface (review P2-3 — this is not an exclusive-lock
  ordering test);
- row-lock/advisory overlap: a multi-statement writer can continue after a
  concurrent single-row writer commits; counter folding never adds a waiting
  edge after the jobs-row lock has already been taken;
- material delete serialization and post-INSERT identity verification under
  in-flight concurrent submissions (#501).
"""

from __future__ import annotations

import threading
import time

import psycopg
import pytest

from server.app.db.connection import connect_database
from server.app.jobs.job_id_encoding import encode_job_id
from server.app.services.job_errors import InvalidOperationError
from server.app.services.run_service import RunService
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers.pg_waits import backend_pid, wait_until_blocked_by
from tests.helpers.run_chunking import WORKFLOW_KEY, WORKSPACE_ID
from tests.helpers.run_chunking import definition_accepting as _definition_accepting
from tests.helpers.run_chunking import insert_materials as _insert_materials
from tests.helpers.run_chunking import material_item as _material_item
from tests.helpers.run_chunking import workspace as _workspace


def _advisory_key(job_db, job_id: str) -> int:
    with job_db.read() as conn:
        row = conn.execute("select hashtext(%s) as k", (f"job-mutation:{job_id}",)).fetchone()
    assert row is not None
    return int(row["k"]) & 0xFFFFFFFFFFFFFFFF


def _advisory_waiters(job_db, job_id: str) -> list[int]:
    """正等待该 job-mutation 锁（granted=False）的 backend pid——键级 pg_locks 观测。

    单键 advisory 锁（objsubid=1）在 pg_locks 里拆成 (classid, objid) 两段
    32 位，按无符号 64 位还原后与 hashtext 比对（同 test_job_create_mutation_lock）。
    """
    expected = _advisory_key(job_db, job_id)
    with job_db.read() as conn:
        rows = conn.execute(
            "select classid, objid, pid from pg_locks"
            " where locktype='advisory' and objsubid=1 and granted=false",
        ).fetchall()
    return [
        int(row["pid"])
        for row in rows
        if ((int(row["classid"]) << 32) | int(row["objid"])) & 0xFFFFFFFFFFFFFFFF == expected
    ]


def _await_advisory_waiter(
    job_db, job_id: str, *, thread: threading.Thread, timeout: float = 10.0
) -> list[int]:
    """等到有 backend 正等待该 job 的 job-mutation advisory 锁（#1342 键级观测）。

    替代 ``wait_until_blocked_by`` 单端探针：pg_blocking_pids 分不出 B 等的是
    advisory 锁还是 A 未提交插入的唯一索引仲裁——删掉创建侧的 advisory 锁，
    B 仍被 A 阻塞、单端探针照样返回（测试假绿）。键级观测直接钉「有等待者
    挂在这把 advisory 键上」。B 提前结束（没走到锁点就写入 / 报错）即失败。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waiters = _advisory_waiters(job_db, job_id)
        if waiters:
            return waiters
        if not thread.is_alive():
            raise AssertionError("waiter thread finished without waiting on the advisory lock")
        time.sleep(0.02)
    raise TimeoutError(f"no backend waiting on job-mutation:{job_id} within {timeout}s")


@pytest.fixture
def service(job_db, settings) -> RunService:
    _workspace(job_db, settings)
    return RunService(job_db, settings)


def test_concurrent_runs_share_material_set_without_row_duplication(
    service, job_db, settings
) -> None:
    """共享 id 并发（review P2-3 的事实表述）：两个并发 create_run（共享
    部分材料集、各自独占一部分）都必须完成且共享材料只建一个 job 行。

    这不是排他锁序测试：两 run 的 FOR KEY SHARE 探测互相兼容（share 锁
    不冲突），锁阶段本身没有跨 run 死锁面；真正钉住的是共享 id 的唯一
    性——确定性 job id + ON CONFLICT DO UPDATE 使后写入者 rebind 而非
    重复建行（与单事务形状在相同竞态下的行为一致）。行级排他冲突的时序
    由 job id 的 ON CONFLICT DO UPDATE 串行化，写入侧无死锁是因为每块
    语句只触达自己的行集。
    """
    _insert_materials(job_db, 120)
    set_a = [f"mat-{i}" for i in range(60)] + [f"mat-{60 + i}" for i in range(30)]
    set_b = [f"mat-{i}" for i in range(60)] + [f"mat-{90 + i}" for i in range(30)]

    results: list[str] = []
    errors: list[BaseException] = []

    def _create(items: list[dict]) -> None:
        try:
            outcome = service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=items)
            results.append(str(outcome["run"]["id"]))
        except BaseException as exc:  # 线程内失败带回主线程
            errors.append(exc)

    thread_a = threading.Thread(target=_create, args=([_material_item(m) for m in set_a],))
    thread_b = threading.Thread(target=_create, args=([_material_item(m) for m in set_b],))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=60)
    thread_b.join(timeout=60)
    assert not thread_a.is_alive() and not thread_b.is_alive()
    assert errors == [], errors
    assert len(results) == 2
    # 去重后的 id 总数 = 60 共享 + 30 + 30 独占 = 120；共享 id 恰一行。
    with job_db.connect() as conn:
        total = conn.execute(
            "select count(*) as n from jobs where workspace_id=%s", (WORKSPACE_ID,)
        ).fetchone()
        distinct = conn.execute(
            "select count(distinct id) as n from jobs where workspace_id=%s", (WORKSPACE_ID,)
        ).fetchone()
    assert int(total["n"]) == 120
    assert int(distinct["n"]) == 120


def test_counter_fold_does_not_close_row_lock_cycle(job_db, settings) -> None:
    """A single-row writer must not wait behind an AFTER-trigger gate.

    A updates job-a and keeps the transaction open. B then takes job-b's row
    lock and commits while A still owns the workspace folder try-lock. Only
    after B has committed does A update job-b. A blocking advisory gate would
    leave B waiting after taking job-b, so A's second statement would close
    the production row-lock × advisory-lock cycle.
    """
    _workspace(job_db, settings)
    _insert_materials(job_db, 2)
    from server.app.services.run_service import RunService as _RS

    svc = _RS(job_db, settings)
    svc.create_run(
        WORKSPACE_ID,
        workflow_key=WORKFLOW_KEY,
        items=[_material_item("mat-0"), _material_item("mat-1")],
    )
    with job_db.connect() as conn:
        rows = conn.execute(
            "select id from jobs where workspace_id=%s order by id", (WORKSPACE_ID,)
        ).fetchall()
    job_a, job_b = str(rows[0]["id"]), str(rows[1]["id"])

    results: dict[str, BaseException | None] = {}
    a_holds_gate = threading.Event()
    release_a = threading.Event()
    b_committed = threading.Event()

    def _writer_a() -> None:
        writer = connect_database(job_db.dsn_identity)
        try:
            with writer:
                writer.execute(
                    "update jobs set title=%s, updated_at=current_timestamp where id=%s",
                    ("rebound-by-a", job_a),
                )
                a_holds_gate.set()
                assert release_a.wait(timeout=15)
                writer.execute(
                    "update jobs set title=%s, updated_at=current_timestamp where id=%s",
                    ("rebound-by-a", job_b),
                )
            results["a"] = None
        except BaseException as exc:  # 线程内失败带回主线程
            results["a"] = exc
        finally:
            writer.close()

    def _writer_b() -> None:
        writer = connect_database(job_db.dsn_identity)
        try:
            assert a_holds_gate.wait(timeout=15)
            with writer:
                writer.execute(
                    "update jobs set title=%s, updated_at=current_timestamp where id=%s",
                    ("rebound-by-b", job_b),
                )
            results["b"] = None
        except BaseException as exc:  # 线程内失败带回主线程
            results["b"] = exc
        finally:
            writer.close()
            b_committed.set()

    thread_a = threading.Thread(target=_writer_a)
    thread_b = threading.Thread(target=_writer_b)
    thread_a.start()
    assert a_holds_gate.wait(timeout=10)
    thread_b.start()
    try:
        assert b_committed.wait(timeout=5), (
            "single-row writer waited behind the AFTER-trigger folder gate"
        )
        assert results.get("b") is None, results
    finally:
        release_a.set()
    thread_a.join(timeout=30)
    thread_b.join(timeout=30)
    assert not thread_a.is_alive() and not thread_b.is_alive()
    assert results == {"b": None, "a": None}, results

    with job_db.connect() as conn:
        titles = {
            str(row["id"]): str(row["title"])
            for row in conn.execute(
                "select id, title from jobs where workspace_id=%s", (WORKSPACE_ID,)
            ).fetchall()
        }
    assert len(titles) == 2
    for job_id in (job_a, job_b):
        assert titles[job_id] == "rebound-by-a", titles

    with job_db.connect() as conn:
        group_by = {
            str(row["status"]): int(row["cnt"])
            for row in conn.execute(
                "select status, count(*) as cnt from jobs where workspace_id=%s group by status",
                (WORKSPACE_ID,),
            ).fetchall()
        }
        counters = {
            str(row["status"]): int(row["cnt"])
            for row in conn.execute(
                "select status, sum(cnt) as cnt from ("
                " select status, cnt from workspace_job_status_counts where workspace_id=%s"
                " union all select status, delta as cnt"
                " from workspace_job_status_count_deltas where workspace_id=%s"
                ") counts group by status having sum(cnt)<>0",
                (WORKSPACE_ID, WORKSPACE_ID),
            ).fetchall()
        }
    assert counters == group_by, (counters, group_by)


def test_material_delete_holding_for_update_blocks_chunk_probe(service, job_db) -> None:
    """material 删除串行化（按块锁下的行为面，P1-1 三序之 (b)）：删除
    事务已持 FOR UPDATE 未提交时，create_run（单块提交，2 items 一块）
    的 FOR KEY SHARE 探测阻塞至删除提交；行已消失 → InvalidOperationError
    + run 行由补偿逻辑清掉，与单事务形状的 TOCTOU 结论一致。多块提交
    时块间删除的对应面由
    test_between_chunks_material_delete_fails_next_chunk_probe 钉住。
    """
    _insert_materials(job_db, 2)

    outcome: list[str] = []
    entered = threading.Event()

    def _create() -> None:
        entered.set()
        try:
            service.create_run(
                WORKSPACE_ID,
                workflow_key=WORKFLOW_KEY,
                items=[_material_item("mat-0"), _material_item("mat-1")],
            )
            outcome.append("created")
        except InvalidOperationError:
            outcome.append("invalid")
        except BaseException as exc:  # 线程内意外失败带回主线程定位
            outcome.append(f"error:{exc!r}")

    holder = connect_database(job_db.dsn_identity)
    try:
        with holder:
            holder.execute(
                "delete from materials where id in ('mat-0','mat-1') and workspace_id=%s",
                (WORKSPACE_ID,),
            )
            thread = threading.Thread(target=_create)
            thread.start()
            assert entered.wait(timeout=5)
            # create_run 应正阻塞在该块的 FOR KEY SHARE 探测上：以 pg_blocking_pids 观测为准。
            wait_until_blocked_by(backend_pid(holder), thread=thread)
            assert thread.is_alive()
        thread.join(timeout=15)
    finally:
        holder.close()

    assert not thread.is_alive()
    assert outcome == ["invalid"]
    with job_db.connect() as conn:
        runs = conn.execute(
            "select count(*) as n from runs where workspace_id=%s", (WORKSPACE_ID,)
        ).fetchone()
    assert int(runs["n"]) == 0


def test_identity_precheck_blocks_on_in_flight_id_insert(service, job_db) -> None:
    """#501（PR #497 review P2-4）：在途的同 id 插入不能让后到者静默 rebind。

    并发提交撞同一 job id：#1255 单射编码退役了 ``col/a`` vs ``col_a``
    的归一冲突后，同 id 异身份的形态只剩同 source_id 不同 source_type——
    material ``col``（A 提交）vs bundle ``col``（B 提交）。两份不同
    items → 不同 digest → 各自独立的 run 行，run upsert 不参与串行化。
    先到者 A 的 INSERT 在触发器里泊车 1.5s（行已插、事务未提交），后到者
    B 的整批预查看不到 A 的在途行（快照读）。#1083 起创建持 job-mutation
    锁到提交，B 阻塞在 A 持有的该 advisory 锁上；A 提交后 B 在锁下、
    INSERT 之前重读身份，发现行上的 (material, col) 与 B 提交的
    (bundle, col) 不一致 → ValueError → B 整 chunk 回滚（未写任何行），
    A 的行保持原 run 绑定。撞 id 竞态的事实面是「别人的 job 被绑到你的
    run」——必须报错。

    同步（#1243 R3）：A 的在途窗口由 pg_stat_activity 观测（A 的 backend 正
    处于触发器的 PgSleep），B 的阻塞由 pg_blocking_pids 观测（被 A 阻塞）。
    旧版用 NOTIFY 判定 A 在途——NOTIFY 只在事务提交时投递，监听方醒来时
    A 其实已提交，B 的「在途」全靠它自己 INSERT 触发同一泊车触发器。"""
    # material `col`（A 提交）与 bundle `col`（B 提交）映射到同一 job id。
    _insert_materials(job_db, 1, prefix="colx")
    with job_db.connect() as conn:
        conn.execute(
            "update materials set id='col' where id='colx-0' and workspace_id=%s",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into material_bundles(id, workspace_id, name, total_size_bytes,"
            " file_count, created_by) values ('col', %s, 'col bundle', 10, 1, 'tester')",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into material_bundle_members(bundle_id, material_id, path, ordinal)"
            " values ('col', 'col', 'col.txt', 0)"
        )
        # A 的 jobs INSERT 落行后泊车（行已插、事务未提交）；主线程经
        # pg_stat_activity 观测到 A 的 backend 处于 PgSleep 后再放 B 进场。
        conn.execute("drop trigger if exists jobs_park_insert on jobs")
        conn.execute("drop function if exists jobs_park_after_insert()")
        conn.execute("""
            create function jobs_park_after_insert() returns trigger as $$
            begin
              perform pg_sleep(1.5);
              return null;
            end $$ language plpgsql
        """)
        conn.execute(
            "create trigger jobs_park_insert after insert on jobs"
            " for each statement execute function jobs_park_after_insert()"
        )

    def _parked_insert_pid(timeout: float = 10.0) -> int:
        deadline = time.monotonic() + timeout
        with psycopg.connect(job_db.dsn_identity, autocommit=True) as monitor:
            while time.monotonic() < deadline:
                row = monitor.execute(
                    "select pid from pg_stat_activity where datname = current_database()"
                    " and wait_event = 'PgSleep' and query ilike %s and pid <> pg_backend_pid()",
                    ("%insert into jobs%",),
                ).fetchone()
                if row is not None:
                    return int(row[0])
                time.sleep(0.01)
        raise AssertionError("A never parked inside its jobs INSERT")

    # widen the entry contract so the bundle item resolves (the seeded demo
    # revision accepts materials only, #156).
    WorkflowRevisionService(job_db).publish_workspace_revision(
        WORKSPACE_ID, _definition_accepting(["material", "bundle"])
    )

    outcome: dict[str, str] = {}

    def _submit(tag: str, item: dict) -> None:
        try:
            service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[item])
            outcome[tag] = "created"
        except InvalidOperationError as exc:
            outcome[tag] = f"invalid:{type(exc.__cause__).__name__}"
        except BaseException as exc:  # 线程内失败带回主线程
            outcome[tag] = f"error:{exc!r}"

    thread_a = threading.Thread(target=_submit, args=("a", _material_item("col")))
    thread_a.start()
    # 撞 id 的 job（material `col` 与 bundle `col` 同图）——B 必须等在这把
    # job-mutation advisory 锁上。
    raced_job_id = encode_job_id(WORKSPACE_ID, WORKFLOW_KEY, "col")
    try:
        _parked_insert_pid()  # A 在途：行已插、事务未提交
        assert thread_a.is_alive(), "A should be parked inside its INSERT"
        thread_b = threading.Thread(
            target=_submit, args=("b", {"type": "bundle", "bundle_id": "col"})
        )
        thread_b.start()
        # B 必须等 A 的在途插入有结论，且等的必须是 job-mutation advisory 锁
        # 本身（键级 pg_locks 观测，#1342）：pg_blocking_pids 单端探针分不出
        # advisory 锁与 A 未提交插入的唯一索引仲裁（删掉创建侧 advisory 锁它
        # 照样返回、测试假绿）。B 提前结束（预查空过后直接写入 / 报错）即失败。
        _await_advisory_waiter(job_db, raced_job_id, thread=thread_b)
        assert "b" not in outcome
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)
    finally:
        with job_db.connect() as conn:
            conn.execute("drop trigger if exists jobs_park_insert on jobs")
            conn.execute("drop function if exists jobs_park_after_insert()")

    assert not thread_a.is_alive() and not thread_b.is_alive(), outcome
    assert outcome["a"] == "created", outcome
    # A 提交后 B 的验证点重读身份：行上是 A 的 (material, col)，B 提交
    # 的是 (bundle, col) → 身份冲突（ValueError cause），B 整 chunk
    # 回滚（跳过未提交），A 的行保持原 run 绑定。
    assert outcome["b"] == "invalid:ValueError", outcome
    with job_db.connect() as conn:
        rows = conn.execute(
            "select count(*) as n, count(distinct id) as d from jobs where workspace_id=%s",
            (WORKSPACE_ID,),
        ).fetchone()
    assert int(rows["n"]) == int(rows["d"]) == 1

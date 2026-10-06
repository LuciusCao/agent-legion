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
from server.app.services.job_errors import InvalidOperationError
from server.app.services.run_service import RunService
from tests.helpers.pg_waits import backend_pid, wait_until_blocked_by
from tests.helpers.run_chunking import WORKFLOW_KEY, WORKSPACE_ID
from tests.helpers.run_chunking import insert_materials as _insert_materials
from tests.helpers.run_chunking import material_item as _material_item
from tests.helpers.run_chunking import workspace as _workspace


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
    """#501（PR #497 review P2-4）：post-INSERT 身份验证封住读后插竞态窗口。

    并发提交撞同一 job id（``col/a`` vs ``col_a`` 归一冲突——两份不同
    items → 不同 digest → 各自独立的 run 行，run upsert 不参与串行化）。
    先到者 A 的 INSERT 在触发器里泊车 1.5s（行已插、事务未提交），后到者
    B 的预查看不到 A 的在途行（快照读；FOR KEY SHARE 对未提交插入本就不
    阻塞——这是实测过的 Postgres 行为），继续进 INSERT 并阻塞在 A 的唯
    一索引仲裁上；A 提交后 B 的 ON CONFLICT rebind 生效，随即 chunk 内的
    verify_chunk_identities 重读身份，发现行上的 (question→material) 身
    份与 B 提交的不一致 → ValueError → B 整 chunk 回滚，A 的行保持原
    run 绑定。无验证版本里 B 的 rebind 静默成功（identity 不校验）——
    撞 id 竞态的事实面是「别人的 job 被绑到你的 run」。"""
    # col/a（A 提交）与 col_a（B 提交）归一到同一 job id。
    _insert_materials(job_db, 1, prefix="colx")
    with job_db.connect() as conn:
        conn.execute(
            "update materials set id='col/a' where id='colx-0' and workspace_id=%s",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values ('col_a', %s, 'hash-col_a', 'col_a.txt', 'text/plain', 10,"
            " 'k', 'ready', 'tester')",
            (WORKSPACE_ID,),
        )
        # A 的 jobs INSERT 落行后泊车（行已插、事务未提交）并 NOTIFY 主
        # 线程——主线程据此确认 A 处于在途窗口后再放 B 进场。
        conn.execute("drop trigger if exists jobs_park_insert on jobs")
        conn.execute("drop function if exists jobs_park_after_insert()")
        conn.execute("""
            create function jobs_park_after_insert() returns trigger as $$
            begin
              perform pg_notify('jobs_parked', '');
              perform pg_sleep(1.5);
              return null;
            end $$ language plpgsql
        """)
        conn.execute(
            "create trigger jobs_park_insert after insert on jobs"
            " for each statement execute function jobs_park_after_insert()"
        )
    listener = psycopg.connect(job_db.dsn_identity, autocommit=True)
    listener.execute("listen jobs_parked")
    import select as _select

    outcome: dict[str, str] = {}

    def _submit(tag: str, item: dict) -> None:
        try:
            service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[item])
            outcome[tag] = "created"
        except InvalidOperationError as exc:
            outcome[tag] = f"invalid:{type(exc.__cause__).__name__}"
        except BaseException as exc:  # 线程内失败带回主线程
            outcome[tag] = f"error:{exc!r}"

    thread_a = threading.Thread(target=_submit, args=("a", _material_item("col/a")))
    thread_a.start()
    try:
        _select.select([listener], [], [], 10)  # 等 A 的泊车通知（行锁在途）
        assert thread_a.is_alive(), "A should be parked inside its INSERT"
        thread_b = threading.Thread(target=_submit, args=("b", _material_item("col_a")))
        thread_b.start()
        # 保留：负向观察窗——B 的在途形态不唯一（预查空过 / 阻塞在 A 的唯一索引仲裁 / 未到验证点），只断言窗内无决定性结果。
        time.sleep(0.6)
        # A 仍在泊车：B 未见决定性结果（预查空过、INSERT 阻塞在 A 的唯一
        # 索引仲裁上，或尚未到达验证点）——B 的最终命运由验证点裁决。
        assert thread_b.is_alive(), "B should still be in flight against A's insert"
        assert "b" not in outcome
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)
    finally:
        listener.close()
        with job_db.connect() as conn:
            conn.execute("drop trigger if exists jobs_park_insert on jobs")
            conn.execute("drop function if exists jobs_park_after_insert()")

    assert not thread_a.is_alive() and not thread_b.is_alive(), outcome
    assert outcome["a"] == "created", outcome
    # A 提交后 B 的验证点重读身份：行上是 A 的 (material, col/a)，B 提交
    # 的是 (material, col_a) → 身份冲突（ValueError cause），B 整 chunk
    # 回滚（rebind 未提交），A 的行保持原 run 绑定。
    assert outcome["b"] == "invalid:ValueError", outcome
    with job_db.connect() as conn:
        rows = conn.execute(
            "select count(*) as n, count(distinct id) as d from jobs where workspace_id=%s",
            (WORKSPACE_ID,),
        ).fetchone()
    assert int(rows["n"]) == int(rows["d"]) == 1

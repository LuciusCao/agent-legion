"""#467 A1–A5：提交路径分块化的正确性协议测试。

Pins the chunked-submit protocol against the guarantees the single-transaction
shape used to give for free:

- chunk boundary: >1000-row submissions land completely (DB truth, not the
  slimmed response) with counters and job_nodes equal to the group-by truth;
- idempotent retry: re-running the same create_jobs_bulk call after a partial
  failure resumes via the dedup/ON CONFLICT arms instead of duplicating rows,
  and the resubmission HEALS the run row (failed→created, count back to the
  run total — #467 review P1-2);
- real-path mid-chunk failure: a candidate that makes chunk 2's INSERT fail
  inside the real chunked loop leaves chunk 1 committed, the run failed with
  progress, and a resubmission resuming the remainder — while a between-
  chunks material delete fails the next chunk's FOR KEY SHARE probe instead
  of letting a dangling reference in (review P1-1/P2-1);
- normalize collision across chunks: ``a/b`` vs ``a_w`` (same job id) is
  rejected over the WHOLE candidate set before the first chunk commits;
- dedup point lookups: filter_existing_dedup_keys returns exactly the
  workspace-scoped subset, regardless of how many other keys exist;
- shared-id concurrency: two concurrent runs over overlapping material sets
  never duplicate a shared job row (deterministic job id + ON CONFLICT);
  FOR KEY SHARE locks are mutually compatible, so the lock phase itself has
  no cross-run deadlock surface (review P2-3 — this is not an exclusive-lock
  ordering test);
- opposite-order exclusive writers: two transactions touching the same job
  rows in opposite orders deadlock by necessity (Postgres breaks it, one
  victim rolls back whole-transaction) — the outcome contract is pinned
  deterministically with a barrier; the real submit path never orders
  multi-row writes across transactions (one chunk statement = one row set).
"""

from __future__ import annotations

import threading
import time

import psycopg
import pytest

from server.app.db.connection import connect_database
from server.app.services.job_errors import InvalidOperationError
from server.app.services.run_partial_failure import PartialRunCreationError
from server.app.services.run_service import RunService
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_builtin_definition

WORKFLOW_KEY = "education_video_problems_generation"
WORKSPACE_ID = "ws-run-chunk"


def _definition_accepting_refs():
    import copy

    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "ref"]
    return workflow_definition_from_dict(raw)


def _workspace(job_db, settings) -> None:
    job_db.create_workspace(WORKSPACE_ID, default_workflow_key=WORKFLOW_KEY)
    from server.app.services.demo_node_seed import seed_demo_workspace_node_codes

    seed_demo_workspace_node_codes(settings, WORKSPACE_ID)
    WorkflowRevisionService(job_db).ensure_active_revision(
        WORKSPACE_ID, load_builtin_definition(WORKFLOW_KEY)
    )


def _insert_materials(job_db, count: int, prefix: str = "mat") -> None:
    with job_db.connect() as conn:
        for i in range(count):
            material_id = f"{prefix}-{i}"
            conn.execute(
                "insert into materials(id, workspace_id, content_hash, filename, content_type,"
                " size_bytes, storage_key, status, created_by)"
                " values (%s, %s, %s, %s, 'text/plain', 10, %s, 'ready', 'tester')"
                " on conflict (id) do nothing",
                (
                    material_id,
                    WORKSPACE_ID,
                    f"hash-{material_id}",
                    f"{material_id}.txt",
                    f"{WORKSPACE_ID}/hash-{material_id}/{material_id}.txt",
                ),
            )


def _material_item(material_id: str) -> dict:
    return {"type": "material", "material_id": material_id}


def _run_job_count(job_db, run_id: str) -> int:
    with job_db.connect() as conn:
        row = conn.execute("select count(*) as n from jobs where run_id=%s", (run_id,)).fetchone()
    return int(row["n"])


@pytest.fixture
def service(job_db, settings) -> RunService:
    _workspace(job_db, settings)
    return RunService(job_db, settings)


def test_create_run_across_chunk_boundary_lands_completely(service, job_db) -> None:
    """>1000 items（chunk 边界之上）：全量落库、job_nodes 齐全、计数器等于
    group-by 真值——DB 真值断言（响应已不再物化 job 行，#467 A4）。"""
    _insert_materials(job_db, 1500)

    result = service.create_run(
        WORKSPACE_ID,
        workflow_key=WORKFLOW_KEY,
        items=[_material_item(f"mat-{i}") for i in range(1500)],
    )

    run_id = result["run"]["id"]
    assert result["created_count"] == 1500
    assert len(result["job_ids"]) == 1500
    assert _run_job_count(job_db, run_id) == 1500
    with job_db.connect() as conn:
        nodes = conn.execute(
            "select count(*) as n from job_nodes j join jobs s on s.id=j.job_id where s.run_id=%s",
            (run_id,),
        ).fetchone()
        truth = conn.execute(
            "select status, count(*) as n from jobs where run_id=%s group by status",
            (run_id,),
        ).fetchall()
        counters = conn.execute(
            "select status, cnt from run_job_status_counts where run_id=%s", (run_id,)
        ).fetchall()
    assert int(nodes["n"]) >= 1500  # every job × every executable node key
    assert {str(r["status"]): int(r["n"]) for r in truth} == {"queued": 1500}
    assert {str(r["status"]): int(r["cnt"]) for r in counters} == {"queued": 1500}


def test_partial_failure_resumable_via_dedup(service, job_db, monkeypatch) -> None:
    """中途失败补偿：chunk 2 失败时 chunk 1 已提交、run 行保留（not-exists
    guard），重提交同一批 items 经 dedup 过滤后只补齐失败部分——分块幂等
    协议的核心恢复路径（等价于 intake 队列的 chunk-error 契约）。"""
    _insert_materials(job_db, 1500)
    items = [_material_item(f"mat-{i}") for i in range(1500)]
    original = job_db.create_jobs_bulk
    calls: list[int] = []

    def _bulk_then_fail_once(**kwargs):
        calls.append(len(kwargs["candidates"]))
        if len(calls) == 1:
            # 在真实插入路径外先真实插入前 1000 条（模拟 chunk 1 已提交），
            # 再抛错模拟 chunk 2 失败：直接调 original 截断 candidates。
            truncated = dict(kwargs, candidates=kwargs["candidates"][:1000])
            original(**truncated)
            raise ValueError("simulated chunk-2 failure")
        return original(**kwargs)

    monkeypatch.setattr(job_db, "create_jobs_bulk", _bulk_then_fail_once)
    with pytest.raises(PartialRunCreationError, match="simulated chunk-2 failure") as caught:
        service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=items)
    monkeypatch.undo()

    # Chunk 1 的 1000 条已提交且 run 行保留（delete_run_without_jobs 的
    # not-exists guard 使补偿变为 no-op——分块协议下这是刻意行为）。
    with job_db.connect() as conn:
        runs = conn.execute(
            "select id, status, error_message, created_count from runs where workspace_id=%s",
            (WORKSPACE_ID,),
        ).fetchall()
    assert len(runs) == 1
    partial_run_id = str(runs[0]["id"])
    assert _run_job_count(job_db, partial_run_id) == 1000
    # 操作者可见性：run 行落 failed（intake 队列 chunk-error 同款状态），
    # error_message 携带已创建进度与恢复指引，created_count 反映部分进度。
    assert str(runs[0]["status"]) == "failed"
    assert "1000 job(s) were already created" in str(runs[0]["error_message"])
    assert int(runs[0]["created_count"]) == 1000
    # 服务层异常携带结构化进度（路由映射为 detail.message/run_id/created_so_far）。
    assert caught.value.created_so_far == 1000
    assert caught.value.run_id == partial_run_id

    # 重提交同一批：dedup 过滤掉已存在的 1000 个键，只插入剩余 500 个。
    # digest 由 items 决定，重提交解析到同一个确定性 run id（upsert）；
    # P1-2 治愈语义：upsert 把 failed→created，created_count 取
    # count_jobs_in_run（整个 run 的 job 切片，天然累计）——run 行回到
    # status='created'、count=1500、error_message=''，详情端点与 job_stats
    # 不再自相矛盾。
    resumed = service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=items)
    assert resumed["created_count"] == 500
    assert resumed["run"]["id"] == partial_run_id
    assert _run_job_count(job_db, partial_run_id) == 1500
    with job_db.connect() as conn:
        healed = conn.execute(
            "select status, error_message, created_count from runs where id=%s",
            (partial_run_id,),
        ).fetchone()
    assert str(healed["status"]) == "created"
    assert int(healed["created_count"]) == 1500
    assert str(healed["error_message"]) == ""
    assert calls == [1500]  # 第一次：1500 候选进入 bulk 前被截断为 1000 并模拟失败


def test_real_chunk_loop_failure_mid_run_and_between_chunks_delete(service, job_db) -> None:
    """真路径 chunk 循环中途失败（review P2-1 钉住）。

    不 monkeypatch bulk 调用：DB 侧装一个 BEFORE INSERT 触发器，凡
    source_id 带 poison 标记的 jobs 行 INSERT 一律 RAISE——毒行排在第
    1001 位，使**真实** chunk 2 的 INSERT 语句失败。断言：chunk 1 的
    1000 条已提交（触发器失败回滚的只有 chunk 2）、run 行 failed 携带
    进度、无 poison job 落库；剔除毒行重提交（1499 项，digest 随 items
    变化 → **新 run 行**，dedup 跳过已建 1000、新 run 承载补齐的 499
    条，旧 failed run 保留失败现场——编排确认过的取舍）。
    """
    _insert_materials(job_db, 1000)
    _insert_materials(job_db, 1, prefix="poison")  # id 带 poison 标记
    _insert_materials(job_db, 499, prefix="tail")
    with job_db.connect() as conn:
        # 测试内的临时守卫（TRUNCATE 隔离不影响其他测试——触发器随本测试
        # 的 schema 清理；普通 DDL 走独立事务，不触碰 fresh_schema 纪律）。
        conn.execute("drop trigger if exists jobs_poison_guard on jobs")
        conn.execute("drop function if exists jobs_poison_reject()")
        conn.execute("""
            create function jobs_poison_reject() returns trigger as $$
            begin
              if new.source_id like 'poison%' then
                raise exception 'poison row rejected by test guard';
              end if;
              return new;
            end $$ language plpgsql
        """)
        conn.execute(
            "create trigger jobs_poison_guard before insert on jobs"
            " for each row execute function jobs_poison_reject()"
        )

    items = [_material_item(f"mat-{i}") for i in range(1000)]
    items.append(_material_item("poison-0"))
    items += [_material_item(f"tail-{i}") for i in range(499)]

    try:
        with pytest.raises(PartialRunCreationError) as caught:
            service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=items)
    finally:
        with job_db.connect() as conn:
            conn.execute("drop trigger if exists jobs_poison_guard on jobs")
            conn.execute("drop function if exists jobs_poison_reject()")

    with job_db.connect() as conn:
        runs = conn.execute(
            "select id, status, error_message, created_count from runs where workspace_id=%s",
            (WORKSPACE_ID,),
        ).fetchall()
    assert len(runs) == 1
    failed_run_id = str(runs[0]["id"])
    # 真实 chunk 1 已提交：恰好 1000 条（毒行使 chunk 2 整块回滚，无部分插入）。
    assert _run_job_count(job_db, failed_run_id) == 1000
    assert str(runs[0]["status"]) == "failed"
    assert "1000 job(s) were already created" in str(runs[0]["error_message"])
    assert caught.value.created_so_far == 1000
    with job_db.connect() as conn:
        poison_jobs = conn.execute(
            "select count(*) as n from jobs where source_id like 'poison%'"
        ).fetchone()
    assert int(poison_jobs["n"]) == 0

    # 剔除毒行后重提（合法 1499 项）：digest 随 items 变化 → **新 run 行**
    # （旧 failed run 与新 run 并存——编排者确认过的取舍：dedup 键保证
    # job 不重复，旧 run 保留其 1000 条与失败现场，新 run 承载补齐的
    # 499 条）。两个 run 各自的计数独立正确。
    healed_items = [_material_item(f"mat-{i}") for i in range(1000)]
    healed_items += [_material_item(f"tail-{i}") for i in range(499)]
    resumed = service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=healed_items)
    assert resumed["run"]["id"] != failed_run_id
    assert resumed["created_count"] == 499
    assert _run_job_count(job_db, failed_run_id) == 1000
    assert _run_job_count(job_db, str(resumed["run"]["id"])) == 499
    with job_db.connect() as conn:
        healed = conn.execute(
            "select status, error_message, created_count from runs where id=%s",
            (str(resumed["run"]["id"]),),
        ).fetchone()
    assert str(healed["status"]) == "created"
    assert int(healed["created_count"]) == 499
    assert str(healed["error_message"]) == ""
    # 旧 failed run 现场保留（操作者审计线索），job 无重复。
    with job_db.connect() as conn:
        failed_row = conn.execute(
            "select status, created_count from runs where id=%s", (failed_run_id,)
        ).fetchone()
        total = conn.execute(
            "select count(*) as n, count(distinct id) as d from jobs where workspace_id=%s",
            (WORKSPACE_ID,),
        ).fetchone()
    assert str(failed_row["status"]) == "failed"
    assert int(failed_row["created_count"]) == 1000
    assert int(total["n"]) == int(total["d"]) == 1499


def test_between_chunks_material_delete_fails_next_chunk_probe(service, job_db) -> None:
    """P1-1 锁序行为面：删除发生在 chunk 1 提交之后、chunk 2 探测之前。

    每块先锁后插（锁随块提交释放）：材料 M 只被 chunk 2 引用时，若 M
    在 chunk 1 提交后、chunk 2 的 FOR KEY SHARE 探测前被删除，chunk 2
    的探测发现行消失 → ValueError → 部分失败路径（无悬挂引用、run
    failed、重提交续传）。这是把锁从「全调用」改回「按块」后的关键
    不变量：任何时序下都不插入引用已删材料的 job。
    """
    _insert_materials(job_db, 1000)  # chunk 1 的引用
    _insert_materials(job_db, 1, prefix="late")  # 仅 chunk 2 引用
    items = [_material_item(f"mat-{i}") for i in range(1000)]
    items.append(_material_item("late-0"))

    deleted = threading.Event()

    def _watcher() -> None:
        # 精确窗口制造（确定性时序）：watcher 先对 late-0 持 FOR UPDATE
        # （在 create_run 开始前），chunk 2 的 FOR KEY SHARE 探测必然阻塞
        # 在这把锁上；watcher 等 jobs 计数到 1000（chunk 1 已提交——它不
        # 引用 late-0，不受影响）后提交删除，被阻塞的 chunk 2 探测醒来
        # 发现行已消失 → ValueError → 部分失败路径。
        deleter = connect_database(job_db.dsn_identity)
        try:
            with deleter:
                deleter.execute(
                    "select id from materials where id='late-0' and workspace_id=%s for update",
                    (WORKSPACE_ID,),
                )
                while not deleted.is_set():
                    row = deleter.execute(
                        "select count(*) as n from jobs where workspace_id=%s", (WORKSPACE_ID,)
                    ).fetchone()
                    if int(row["n"]) >= 1000:
                        # 引用检查此刻看不到 late-0 的 job（chunk 2 被锁挡住），
                        # 删除守卫放行；提交后行消失、行锁释放。
                        deleter.execute(
                            "delete from materials where id='late-0' and workspace_id=%s",
                            (WORKSPACE_ID,),
                        )
                        deleted.set()
                        return
                    time.sleep(0.005)
        finally:
            deleter.close()

    watcher = threading.Thread(target=_watcher)
    watcher.start()
    try:
        with pytest.raises(PartialRunCreationError, match="Material not found"):
            service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=items)
    finally:
        deleted.wait(timeout=5)
        watcher.join(timeout=10)

    # chunk 1 的 1000 条保留；late-0 的 job 从未插入（无悬挂引用）。
    with job_db.connect() as conn:
        jobs = conn.execute(
            "select count(*) as n from jobs where workspace_id=%s", (WORKSPACE_ID,)
        ).fetchone()
        dangling = conn.execute(
            "select count(*) as n from jobs"
            " where input_json::jsonb ->> 'type' = 'material'"
            " and input_json::jsonb ->> 'material_id' = 'late-0'",
        ).fetchone()
    assert int(jobs["n"]) == 1000
    assert int(dangling["n"]) == 0


def test_intra_run_normalize_collision_detected_across_chunks(service, job_db) -> None:
    """normalize 冲突跨块检测：两个归一到同一 job id 的 source_id（``a/b``
    vs ``a_w``）无论落在候选集的哪个位置，都必须在首个 chunk 提交前被
    拒绝——冲突检测覆盖整个候选集（Python dict 期），不是逐块独立。"""
    _insert_materials(job_db, 1200)
    _insert_materials(job_db, 2, prefix="col")
    with job_db.connect() as conn:
        conn.execute(
            "update materials set id='col/a' where id='col-0' and workspace_id=%s",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "update materials set id='col_a' where id='col-1' and workspace_id=%s",
            (WORKSPACE_ID,),
        )

    items = [_material_item(f"mat-{i}") for i in range(1200)] + [
        _material_item("col/a"),
        _material_item("col_a"),
    ]

    with pytest.raises(InvalidOperationError, match="Job identity collision"):
        service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=items)

    # 整个请求零写入：run 行与 jobs 都不存在。
    with job_db.connect() as conn:
        runs = conn.execute(
            "select count(*) as n from runs where workspace_id=%s", (WORKSPACE_ID,)
        ).fetchone()
        jobs = conn.execute(
            "select count(*) as n from jobs where workspace_id=%s", (WORKSPACE_ID,)
        ).fetchone()
    assert int(runs["n"]) == 0
    assert int(jobs["n"]) == 0


def test_filter_existing_dedup_keys_point_lookup_semantics(service, job_db) -> None:
    """A2 点查语义：只返回请求键中已存在的子集，workspace 作用域正确，
    未涉及的键（哪怕同 workspace 存在大量其他 job）不影响结果。"""
    _insert_materials(job_db, 3)
    service.create_run(
        WORKSPACE_ID,
        workflow_key=WORKFLOW_KEY,
        items=[_material_item("mat-0"), _material_item("mat-1")],
    )

    keys = [("material", "mat-0"), ("material", "mat-1"), ("material", "mat-2")]
    assert job_db.filter_existing_dedup_keys(WORKSPACE_ID, keys) == {
        ("material", "mat-0"),
        ("material", "mat-1"),
    }
    # 请求内重复键无害（set 语义）。
    assert job_db.filter_existing_dedup_keys(
        WORKSPACE_ID, [("material", "mat-0"), ("material", "mat-0")]
    ) == {("material", "mat-0")}
    # 空/不存在键 → 空集。
    assert job_db.filter_existing_dedup_keys(WORKSPACE_ID, []) == set()
    assert job_db.filter_existing_dedup_keys(WORKSPACE_ID, [("material", "nope")]) == set()


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


def test_per_row_updates_in_opposite_order_deadlock_and_recover(job_db, settings) -> None:
    """P2-3 的排他锁事实面（barrier 钉住，确定性）：两个事务以相反
    顺序先后 UPDATE 同一对 job 行，交错持锁 → **必然死锁**（Postgres
    检测并回滚一个受害者，50/50 脚本实测），幸存者提交、受害者整事务
    回滚零残留——这正是仓库里 40P01 单次重试基建（agent_broker
    claim_retry、db/retry）所吸收的并发事实，不是 jobs 写入路径需要
    规避的新死锁面：真实提交路径一次触达一行（分块 INSERT + ON
    CONFLICT 的 resubmit 臂同语句锁同一行集），不存在事务间反序。

    两个「反序并发必然死锁」的形态（复审轮先后实测，均 50/50）：
    (a) 一条多行 ON CONFLICT 语句——投机插入全部候选后逐个冲突仲裁，
    两事务各持一行锁再互相等待；(b) 逐行 UPDATE ×2——第二行等第一行
    的行锁。因此「相反顺序无死锁」不是 jobs 写入路径的可断言性质；
    本测试钉住的是死锁的**结果**契约（受害者回滚、幸存者落库、无
    重复无丢行），并以 barrier 把 (b) 的窗口从偶发变成确定性。
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

    # barrier 精确交错：两线程各锁住自己「第一行」后再去要对方的行。
    barrier = threading.Barrier(2, timeout=15)
    results: dict[str, BaseException | None] = {}

    def _rebind(tag: str, ids: list[str]) -> None:
        writer = connect_database(job_db.dsn_identity)
        try:
            with writer:
                writer.execute(
                    "update jobs set title=%s, updated_at=current_timestamp where id=%s",
                    (f"rebound-by-{tag}", ids[0]),
                )
                barrier.wait()  # 双方都持住第一行的锁，再交叉请求
                writer.execute(
                    "update jobs set title=%s, updated_at=current_timestamp where id=%s",
                    (f"rebound-by-{tag}", ids[1]),
                )
            results[tag] = None
        except BaseException as exc:  # 线程内失败带回主线程
            results[tag] = exc
        finally:
            writer.close()

    t1 = threading.Thread(target=_rebind, args=("w1", [job_a, job_b]))
    t2 = threading.Thread(target=_rebind, args=("w2", [job_b, job_a]))
    t1.start()
    t2.start()
    t1.join(timeout=60)
    t2.join(timeout=60)
    assert not t1.is_alive() and not t2.is_alive()

    outcomes = list(results.values())
    deadlocked = [exc for exc in outcomes if isinstance(exc, psycopg.errors.DeadlockDetected)]
    # 死锁受害者恰一个；幸存者的两行 UPDATE 都在（其事务提交）。
    assert len(deadlocked) == 1, outcomes
    survivor = [tag for tag, exc in results.items() if exc is None]
    assert len(survivor) == 1, results
    with job_db.connect() as conn:
        final = conn.execute(
            "select count(*) as n, count(distinct id) as d from jobs where workspace_id=%s",
            (WORKSPACE_ID,),
        ).fetchone()
        titles = conn.execute(
            "select distinct title from jobs where workspace_id=%s", (WORKSPACE_ID,)
        ).fetchall()
    # 无重复、无丢行；幸存者的 title 覆盖两行（受害者的写入整事务回滚）。
    assert int(final["n"]) == int(final["d"]) == 2
    assert {str(row["title"]) for row in titles} == {f"rebound-by-{survivor[0]}"}


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
            time.sleep(0.5)  # create_run 应正阻塞在该块的 FOR KEY SHARE 探测上
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

"""#467 A1–A5：提交路径分块化的正确性协议测试。

Pins the chunked-submit protocol against the guarantees the single-transaction
shape used to give for free:

- chunk boundary: >1000-row submissions land completely (DB truth, not the
  slimmed response) with counters and job_nodes equal to the group-by truth;
- idempotent retry: re-running the same create_jobs_bulk call after a partial
  failure resumes via the dedup/ON CONFLICT arms instead of duplicating rows;
- failure compensation: a chunk-2 failure leaves chunk-1 rows committed and
  the run row in place (delete_run_without_jobs's not-exists guard), and a
  BEFORE-first-chunk failure still compensates the run away;
- normalize collision across chunks: ``a/b`` vs ``a_w`` (same job id) is
  rejected over the WHOLE candidate set before the first chunk commits;
- dedup point lookups: filter_existing_dedup_keys returns exactly the
  workspace-scoped subset, regardless of how many other keys exist;
- lock ordering: FOR KEY SHARE locks whole-call rows up front (materials →
  bundles), so a delete cannot interleave between two insert chunks.
"""

from __future__ import annotations

import threading
import time

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
    # digest 由 items 决定，重提交解析到同一个确定性 run id（upsert），
    # 因此 500 条新 job 落回同一 run 行，计数由 1000 恢复为完整 1500。
    resumed = service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=items)
    assert resumed["created_count"] == 500
    assert resumed["run"]["id"] == partial_run_id
    assert _run_job_count(job_db, partial_run_id) == 1500
    assert calls == [1500]  # 第一次：1500 候选进入 bulk 前被截断为 1000 并模拟失败


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


def test_concurrent_run_creation_shares_no_lock_order_deadlock(service, job_db, settings) -> None:
    """锁序论证的行为面：两个并发 create_run（共享部分材料集、各自独占
    一部分）都必须完成——FOR KEY SHARE IN 锁按 ORDER BY id 顺序获取，
    避免两请求以相反顺序拿锁导致死锁。"""
    _insert_materials(job_db, 120)
    # 两批材料：共享 60 个 + 各自独占 30 个；id 交错放大乱序风险。
    # 各自独占不同 source_type 命名空间（dedup key 是 (source_type, source_id)
    # 而 job id 只由 source_id 派生）：用 ref item（entity_id 带连接前缀）保证
    # 两线程的共享材料 dedup 出同一 job id、独占材料互不冲突。
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
    # 共享材料只建一个 job 行（确定性 job id + ON CONFLICT DO UPDATE：并发点查
    # dedup 都视其为 fresh，后写入者把行 rebind 到自己的 run——与单事务形状在
    # 同样竞态下的行为一致，非本次改动引入）。去重后的 id 总数 = 60 共享 + 30
    # + 30 独占 = 120。
    with job_db.connect() as conn:
        total = conn.execute(
            "select count(*) as n from jobs where workspace_id=%s", (WORKSPACE_ID,)
        ).fetchone()
        distinct = conn.execute(
            "select count(distinct id) as n from jobs where workspace_id=%s", (WORKSPACE_ID,)
        ).fetchone()
    assert int(total["n"]) == 120  # 60 shared + 30 + 30 distinct ids
    assert int(distinct["n"]) == 120


def test_material_delete_blocks_on_whole_call_lock(service, job_db) -> None:
    """material 删除串行化在分块锁下重新论证（行为面）：删除事务已持
    FOR UPDATE 未提交时，create_run 的 FOR KEY SHARE IN 批量锁阻塞至删除
    提交；行已消失 → InvalidOperationError，与单事务形状的 TOCTOU 结论
    一致（run 行由补偿逻辑清掉）。"""
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
            time.sleep(0.5)  # create_run 应正阻塞在批量 FOR KEY SHARE 上
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

"""#1083：job 创建进入 ``job-mutation`` 锁域（#1065 follow-up）。

删除的提交后清理在 ``job-mutation:<id>`` 锁下复核 jobs 行不存在，再把 job_dir
rename 入 ``.trash``。创建路径（``create_job`` / ``create_jobs_bulk``）此前不取
锁且先 mkdir 再 upsert：同源重建若落在「复核 → rename」之间，它采用的目录会被
整体移走。现在创建在 mkdir / upsert 前取同一把锁并持到提交。

同步纪律（等信号不等时长）：竞态点用 monkeypatch 嵌进真实路径（清理锁内的
``stage_path``），创建方在另一线程发起，经 pg_locks 观测「它正等待该 advisory
锁」后才放行；join 带超时并断言线程已结束，防假绿。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

import server.app.services.job_deletion_trash as trash_module
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.job_id_encoding import encode_job_id
from server.app.services.job_deletion import JobDeletionService
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir

_WORKFLOW = "demo_workflow"
_REVISION = {"id": "rev-1083", "version": 1, "definition_hash": "h", "definition_json": "{}"}


def _settings(tmp_path: Path) -> Settings:
    # 与 job_db fixture 的路径一致（load_settings(data_dir=tmp_path) → tmp_path/jobs）。
    for name in ["jobs", "logs", "videos", "packages"]:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
    )


def _new_run(job_db: JobQueries, workspace_id: str, source_ids: list[str]) -> str:
    batch = job_db.create_run(_WORKFLOW, "batch_by_ids", {"question_ids": source_ids}, workspace_id)
    return str(batch["id"])


def _create_single(job_db: JobQueries, workspace_id: str, source_id: str) -> str:
    job = job_db.create_job(
        _WORKFLOW,
        "question",
        source_id,
        _new_run(job_db, workspace_id, [source_id]),
        f"Job {source_id}",
        ["extract_question"],
        workspace_id=workspace_id,
    )
    return str(job["id"])


def _create_bulk(job_db: JobQueries, workspace_id: str, source_ids: list[str]) -> list[str]:
    return job_db.create_jobs_bulk(
        candidates=[
            {"entity_id": sid, "entity_type": "question", "title": sid, "stem": sid}
            for sid in source_ids
        ],
        workflow_key=_WORKFLOW,
        run_id=_new_run(job_db, workspace_id, source_ids),
        node_keys=["extract_question"],
        workspace_id=workspace_id,
        revision=_REVISION,
    )


def _advisory_key(job_db: JobQueries, job_id: str) -> int:
    with job_db.read() as conn:
        row = conn.execute("select hashtext(%s) as k", (f"job-mutation:{job_id}",)).fetchone()
    assert row is not None
    return int(row["k"]) & 0xFFFFFFFFFFFFFFFF


def _advisory_holders(job_db: JobQueries, job_id: str, *, granted: bool) -> list[int]:
    """持有（granted）或等待（not granted）该 job-mutation 锁的 backend pid。

    单键 advisory 锁（objsubid=1）在 pg_locks 里拆成 (classid, objid) 两段 32 位，
    按无符号 64 位还原后与 hashtext 比对（同 tests/db 的 generation 竞态测试）。
    """
    expected = _advisory_key(job_db, job_id)
    with job_db.read() as conn:
        rows = conn.execute(
            "select classid, objid, pid from pg_locks"
            " where locktype='advisory' and objsubid=1 and granted=%s",
            (granted,),
        ).fetchall()
    return [
        int(row["pid"])
        for row in rows
        if ((int(row["classid"]) << 32) | int(row["objid"])) & 0xFFFFFFFFFFFFFFFF == expected
    ]


def _await_waiter(job_db: JobQueries, job_id: str, timeout: float = 10.0) -> list[int]:
    """等到有 backend 正等待该 job 的 job-mutation 锁；返回等待者 pid（超时为空）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waiters = _advisory_holders(job_db, job_id, granted=False)
        if waiters:
            return waiters
        time.sleep(0.02)
    return []


def _start(fn: Any) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["result"] = fn()
        except Exception as exc:  # 线程内异常交回主线程断言
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=30)
    assert not thread.is_alive(), "creating transaction never resolved"


@pytest.mark.parametrize("path", ["create_job", "create_jobs_bulk"])
def test_recreate_inside_cleanup_recheck_window_waits_and_keeps_its_dir(
    job_db: JobQueries, tmp_path: Path, monkeypatch, path: str
) -> None:
    """#1065 R1 窗口：清理已在锁下复核「行不存在」、尚未 rename 时同源重建。

    重建必须阻塞在 job-mutation 锁上（不得先 mkdir / upsert），rename 把旧目录
    移走后才落地：新 job 有行、有自己的空目录，旧 job 的文件不会被它继承，
    也不会连同新 job 的目录一起被移入 trash。
    """
    settings = _settings(tmp_path)
    workspace_id = f"ws-1083-{path.replace('_', '-')}"
    job_db.create_workspace(workspace_id)
    job_id = _create_single(job_db, workspace_id, "Q1083")
    job = job_db.get_job(job_id)
    assert job is not None
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    (storage_dir / "old.json").write_text("old", encoding="utf-8")
    service = JobDeletionService(
        job_db, ExecutorLeaseRepository(job_db, data_dir=tmp_path), settings
    )

    def recreate() -> Any:
        if path == "create_job":
            return _create_single(job_db, workspace_id, "Q1083")
        return _create_bulk(job_db, workspace_id, ["Q1083"])

    original_stage = trash_module.stage_path
    observed: dict[str, Any] = {}

    def stage_after_racing_recreate(target: Path, *args: Any, **kwargs: Any) -> list[Path]:
        if target == storage_dir and "thread" not in observed:
            # 此刻：删除已提交、清理持锁并已复核行不存在、rename 尚未发生。
            observed["thread"], observed["outcome"] = _start(recreate)
            observed["waiters"] = _await_waiter(job_db, job_id)
            observed["row_while_blocked"] = job_db.get_job(job_id)
        return original_stage(target, *args, **kwargs)

    monkeypatch.setattr(trash_module, "stage_path", stage_after_racing_recreate)

    result = service.delete(workspace_id, job_id)

    _join(observed["thread"])
    assert "error" not in observed["outcome"], observed["outcome"].get("error")
    assert result["status"] == "succeeded"
    assert observed["waiters"], "recreate did not wait on job-mutation inside the window"
    assert observed["row_while_blocked"] is None
    recreated = job_db.get_job(job_id)
    assert recreated is not None
    assert resolve_job_dir(recreated, settings.jobs_dir) == storage_dir
    assert storage_dir.is_dir(), "recreated job's directory was moved into .trash"
    assert list(storage_dir.iterdir()) == []


def _insert_legacy_job(
    job_db: JobQueries, workspace_id: str, legacy_id: str, source_id: str
) -> None:
    """pre-v62 / pre-#1255 形态的存量行，直插 SQL 播种。

    legacy 行的 workspace id 或 source_id 可能带 ``~``（v62 起 workspace id
    契约禁 ``~``，create_workspace 会拒绝），job id 是裸拼接而非转义编码，
    两个面都只能绕开现行创建路径模拟（同
    tests/db/test_job_bulk_injective_ids.py 的 _insert_legacy_job）。
    """
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, %s) on conflict (id) do nothing",
            (workspace_id, workspace_id),
        )
        conn.execute(
            "insert into runs(id, workspace_id, source_kind)"
            " values (%s, %s, 'items') on conflict (id) do nothing",
            (f"run-{workspace_id}", workspace_id),
        )
        conn.execute(
            """
            insert into jobs(
              id, workspace_id, source_type, source_id, run_id, title, storage_dir, stem,
              workflow_revision_id, workflow_version, workflow_definition_hash,
              workflow_definition_snapshot_json
            )
            values (%s, %s, 'question', %s, %s, 'legacy', 'legacy/dir', '', '', 1, '', '')
            """,
            (legacy_id, workspace_id, source_id, f"run-{workspace_id}"),
        )


def test_bulk_create_rejects_cross_workspace_id_collision_before_locking(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """#1243 codex P2（#1255 单射编码后的残余形态）：跨 workspace 撞 id 只剩
    跨时代巧合——存量 pre-v62 legacy 行的 workspace id 含 ``~``，其裸拼接 id
    恰等于本 workspace 某候选的新编码。预检必须在取任何 job-mutation 锁之前
    以结构化 identity 错误拒绝——即便该行的锁正被别人持有，创建也不等待、
    不建目录、不写行。"""
    own_ws = "w1243_x"
    job_db.create_workspace(own_ws)
    # legacy_ws = 编码首段（own_ws 的 ``_`` 转义为 ``~u``）；legacy 行按裸
    # 拼接恰好得到同一个 id，但 workspace_id 是别人。
    legacy_id = encode_job_id(own_ws, _WORKFLOW, "y")
    legacy_ws = legacy_id.split("_")[0]
    assert legacy_ws != own_ws and "~" in legacy_ws
    _insert_legacy_job(job_db, legacy_ws, legacy_id, "y")
    other_id = encode_job_id(own_ws, _WORKFLOW, "zz")

    with job_db.job_mutation_lock(legacy_id) as exists:
        assert exists is True
        thread, outcome = _start(lambda: _create_bulk(job_db, own_ws, ["y", "zz"]))
        thread.join(timeout=30)
        assert not thread.is_alive(), "bulk create waited on a foreign job's lock"
        assert _advisory_holders(job_db, legacy_id, granted=False) == []

    assert isinstance(outcome.get("error"), ValueError), outcome
    assert "collision" in str(outcome["error"]).lower()
    assert job_db.get_job(other_id) is None
    assert not any((tmp_path / "jobs").rglob(other_id))


def test_bulk_create_rejects_late_chunk_collision_before_any_commit(
    job_db: JobQueries, tmp_path: Path, monkeypatch
) -> None:
    """#1243 codex R3：碰撞落在后面的 chunk 时，前面的 chunk 也不得已提交——
    整批身份预检在首个 chunk 提交之前完成。#1255 后跨行碰撞的残余形态是跨
    时代巧合：存量 legacy 行（source_id 含 ``~``）的裸拼接 id 恰等于某候选
    的新编码。"""
    import server.app.jobs.queries.job_bulk as job_bulk_module

    monkeypatch.setattr(job_bulk_module, "CREATE_CHUNK_ROWS", 1)
    own_ws = "w1243b"
    job_db.create_workspace(own_ws)
    # legacy 行 source_id "c~ud" 的裸拼接 id == encode(own_ws, _WORKFLOW, "c_d")。
    legacy_id = encode_job_id(own_ws, _WORKFLOW, "c_d")
    _insert_legacy_job(job_db, own_ws, legacy_id, "c~ud")
    first_id = encode_job_id(own_ws, _WORKFLOW, "aa")

    with pytest.raises(ValueError, match="collision"):
        _create_bulk(job_db, own_ws, ["aa", "c_d"])

    assert job_db.get_job(first_id) is None
    assert not any((tmp_path / "jobs").rglob(first_id))


def _lock_under_sort(node: dict[str, Any], sort_above: bool = False) -> bool:
    """True if some plan node evaluating pg_advisory_xact_lock has a Sort ancestor."""
    evaluates = any("pg_advisory_xact_lock" in out for out in node.get("Output", []))
    if evaluates and sort_above:
        return True
    below = sort_above or node.get("Node Type") == "Sort"
    return any(_lock_under_sort(child, below) for child in node.get("Plans", []))


def _find_sort(node: dict[str, Any]) -> dict[str, Any] | None:
    if node.get("Node Type") == "Sort":
        return node
    for child in node.get("Plans", []):
        found = _find_sort(child)
        if found is not None:
            return found
    return None


def test_set_based_lock_statement_locks_above_the_lock_key_sort(job_db: JobQueries) -> None:
    """#1346：一条集合语句取完全部锁，取锁序必须跟在「真实锁键」的 Sort 之后。

    锁资源是 32 位 hashtext(key) 而非 id 文本：#1243 codex R2 的 ordinality
    形态（ORDER BY ord 省掉 Sort）只保住「按数组序」，数组序是 id 文本序——
    碰撞四元组会把两个并发 chunk 折叠成 AB-BA。现在 ORDER BY 直接建在
    (hashtext, id) 上：计划必须带 Sort（键含 hashtext），且 volatile 的
    pg_advisory_xact_lock 求值于 Sort 之上（make_sort_input_target 不把
    volatile 目标列压进排序输入）——取锁严格按排序后的 (锁键, id) 逐行发生。
    真实取锁顺序由下方 pg_locks 用例与碰撞四元组死锁用例覆盖。"""
    from server.app.jobs.queries.job_bulk_sql import LOCK_JOB_MUTATIONS_SQL

    with job_db.connect() as conn:
        plan = conn.execute(
            "explain (verbose, format json) " + LOCK_JOB_MUTATIONS_SQL, (["c", "a", "b"],)
        ).fetchone()
    assert plan is not None
    root = next(iter(plan.values()))[0]["Plan"]
    assert "pg_advisory_xact_lock" in str(root)
    # 锁求值节点上方不得有 Sort（先取锁再排序 = 取锁序退化为扫描序）。
    assert not _lock_under_sort(root), root
    # 且必须确有按真实锁键的 Sort 在锁求值之下（否则 ORDER BY 没建在
    # hashtext 上——例如退化回 id 文本序——取锁序就不再是锁键序）。
    sort = _find_sort(root)
    assert sort is not None, "ORDER BY the actual lock key must plan a Sort"
    assert any("hashtext" in str(key) for key in sort.get("Sort Key", [])), sort


def _signed_advisory_key(job_db: JobQueries, job_id: str) -> int:
    """该 job-mutation 锁的 32 位 hashtext 键（有符号，即 PG 排序所用的域）。"""
    with job_db.read() as conn:
        row = conn.execute("select hashtext(%s)::int as k", (f"job-mutation:{job_id}",)).fetchone()
    assert row is not None
    return int(row["k"])


def test_bulk_create_takes_job_mutation_locks_in_lock_key_order(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """锁序防死锁（#1346）：bulk chunk 按 (实际 advisory 锁键, job_id) 升序取锁
    ——全局批序在单 workspace 上的形态，与输入顺序及 id 文本序无关。

    三个 id 按真实锁键排序后，居中的那把被占住时，bulk 必须已持有锁键更小的
    id、尚未碰锁键更大的 id，且尚未建任何目录或写任何行。夹具刻意挑「键序
    最大者的 id 文本小于键序居中者」的三元组：退回 id 文本序的 mutation 会
    先拿到键序最大者的锁再阻塞，「未碰较大侧」断言必红（deterministic
    mutation 覆盖）。"""
    workspace_id = "ws-1083-order"
    job_db.create_workspace(workspace_id)
    with job_db.read() as conn:
        pool: list[tuple[str, str, int]] = []
        for i in range(60):
            source_id = f"s{i:02d}"
            job_id = encode_job_id(workspace_id, _WORKFLOW, source_id)
            row = conn.execute(
                "select hashtext('job-mutation:' || %s)::int as k", (job_id,)
            ).fetchone()
            assert row is not None
            pool.append((source_id, job_id, int(row["k"])))
    by_key = sorted(pool, key=lambda p: (p[2], p[1]))
    chosen: tuple[tuple[str, str, int], ...] | None = None
    for j in range(len(by_key) - 2):
        triple = tuple(by_key[j : j + 3])
        if triple[2][1] < triple[1][1]:  # 键序最大者的文本 < 键序居中者的文本
            chosen = triple
            break
    assert chosen is not None, "60 ids must yield a text/key inverted triple"
    (_, first_id, _), (_, middle_id, _), (_, last_id, _) = chosen
    source_ids = {job_id: source for source, job_id, _ in pool}
    wanted = [first_id, middle_id, last_id]

    with job_db.job_mutation_lock(middle_id) as exists:
        assert exists is False
        thread, outcome = _start(
            lambda: _create_bulk(
                job_db, workspace_id, [source_ids[job_id] for job_id in reversed(wanted)]
            )
        )
        waiters = _await_waiter(job_db, middle_id)
        assert waiters, "bulk create never waited on the held job-mutation lock"
        assert _advisory_holders(job_db, first_id, granted=True) == waiters
        assert _advisory_holders(job_db, last_id, granted=True) == []
        assert [job_id for job_id in wanted if job_db.get_job(job_id) is not None] == []
        assert not any((tmp_path / "jobs").rglob(f"{workspace_id}_*"))

    _join(thread)
    assert "error" not in outcome, outcome.get("error")
    assert sorted(outcome["result"]) == sorted(wanted)
    for job_id in wanted:
        job = job_db.get_job(job_id)
        assert job is not None
        assert resolve_job_dir(job, tmp_path / "jobs").is_dir()


def _colliding_quadruple(job_db: JobQueries) -> tuple[str, str, str, str]:
    """找 a<b<c<d（文本序）且 hash(a)=hash(d)、hash(b)=hash(c) 的碰撞四元组——
    #1346 的 AB-BA 形态：两个 hashtext 碰撞组按包含关系嵌套（inner 严格落在
    outer 的 [min, max] 内）。零填充数值 id 使文本序 == 数值序；一条服务端
    聚合取回全部碰撞组（3M 行约千组，嵌套对必然存在），避免逐值往返。"""
    with job_db.read() as conn:
        rows = conn.execute(
            """
            select k, array_agg(id order by id) as ids from (
              select lpad(g::text, 7, '0') as id,
                     hashtext('job-mutation:' || lpad(g::text, 7, '0')) as k
              from generate_series(1, 3000000) g
            ) h group by k having count(*) > 1
            """
        ).fetchall()
    groups = [[str(i) for i in row["ids"]] for row in rows]
    assert len(groups) >= 2, "3M ids must produce multiple hashtext collision groups"
    for outer in groups:
        for inner in groups:
            if inner is not outer and outer[0] < inner[0] and inner[-1] < outer[-1]:
                return outer[0], inner[0], inner[-1], outer[-1]
    raise AssertionError("no nested hashtext collision quadruple among the groups")


def test_lock_job_mutations_sorted_no_ab_ba_on_colliding_keys(job_db: JobQueries) -> None:
    """#1346 死锁回归：碰撞四元组上两个并发 chunk 不再按相反顺序等同两把锁。

    a<b<c<d 且 hash(a)=hash(d)、hash(b)=hash(c)：按 id 文本序时 chunk [a,b]
    的取锁序为 hash(a)→hash(b)、chunk [c,d] 为 hash(b)→hash(a)（c 的键 ==
    hash(b)，d 的键 == hash(a)）——两把实际锁、相反顺序，并发即 40P01。按
    (锁键, id) 序时两个 chunk 都先取锁键较小侧：后到者原地等待且手中不持
    较大侧那把锁——本用例钉的就是这个可观测差（旧代码下后到者先拿了较大侧
    的锁，「持有集合」断言必红，即 mutation 自检的抓点）。

    时序（等信号非等时长）：chunk1 两把锁都拿到（pg_locks 钉住）后才放
    chunk2 进场；chunk2 的等待经键级 granted=False 观测确认。"""
    from server.app.jobs.queries.job_bulk_sql import lock_job_mutations_sorted

    a, b, c, d = _colliding_quadruple(job_db)
    keys = [_signed_advisory_key(job_db, x) for x in (a, b, c, d)]
    assert keys[0] == keys[3] and keys[1] == keys[2] and keys[0] != keys[1]
    lo_id, hi_id = (a, b) if keys[0] < keys[1] else (b, a)

    entered = threading.Event()
    release = threading.Event()

    def _hold_first_chunk() -> None:
        with job_db.connect() as conn:
            lock_job_mutations_sorted(conn, [a, b])
            entered.set()
            assert release.wait(timeout=15)

    holder_thread, holder_outcome = _start(_hold_first_chunk)
    try:
        assert entered.wait(timeout=10), "first chunk never acquired its locks"
        holder_pid = _advisory_holders(job_db, a, granted=True)
        assert len(holder_pid) == 1
        assert _advisory_holders(job_db, b, granted=True) == holder_pid

        def _second_chunk() -> None:
            with job_db.connect() as conn:
                lock_job_mutations_sorted(conn, [c, d])

        waiter_thread, waiter_outcome = _start(_second_chunk)
        try:
            waiters = _await_waiter(job_db, lo_id)
            assert waiters, "second chunk never waited on the smaller lock key"
            # 等待中的 chunk2 不得持有锁键较大侧的那把锁（旧 id 序下它持有）。
            assert _advisory_holders(job_db, hi_id, granted=True) == holder_pid
        finally:
            release.set()
            _join(waiter_thread)
        assert "error" not in waiter_outcome, waiter_outcome.get("error")
    finally:
        release.set()
        _join(holder_thread)
    assert "error" not in holder_outcome, holder_outcome.get("error")


def test_bulk_create_under_lock_recheck_rejects_raced_row_before_mkdir(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """#1343（#1243 复验 P2-2）：检查点 (2)（锁下、INSERT 前复核）的独有角色——
    在途异身份行于 (1) 整批预检之后、锁下复核之前提交时，(2) 在 mkdir / 写入
    之前拒绝，不留 stray 目录。

    编排（等信号非等时长）：主线程先持该 id 的 job-mutation 锁 → B 线程的
    create_jobs_bulk 越过 (1)（行尚未提交，快照读不到）后阻塞在锁上（键级
    pg_locks 观测确认）→ 主线程在另一连接直插并提交异身份行（legacy 形态，
    不建目录，残留才可观测）→ 放锁 → B 在锁下经 (2) 看到该行、ValueError
    整 chunk 回滚。去掉 (2) 时 B 按 (1) 的过期存在图 mkdir 出 stray 目录、
    再被 (3) 兜底拒绝——目录断言即 (2) 的独立 mutation 覆盖。"""
    from server.app.jobs.storage_layout import job_storage_dir

    workspace_id = "ws-1343"
    job_db.create_workspace(workspace_id)
    job_id = encode_job_id(workspace_id, _WORKFLOW, "raced")
    storage_dir = job_storage_dir(tmp_path / "jobs", workspace_id, job_id)

    thread: threading.Thread | None = None
    outcome: dict[str, Any] = {}
    try:
        with job_db.job_mutation_lock(job_id) as exists:
            assert exists is False
            thread, outcome = _start(lambda: _create_bulk(job_db, workspace_id, ["raced"]))
            waiters = _await_waiter(job_db, job_id)
            assert waiters, "bulk create never reached the job-mutation lock"
            # B 已越过检查点 (1)（行此时仍不存在）、正等锁；此刻提交异身份行
            # （同 id、不同 source_id），(2) 将在锁下看到它。
            _insert_legacy_job(job_db, workspace_id, job_id, "raced-other-source")
        # 锁随 with 退出释放，B 继续。
        _join(thread)
    finally:
        # 守卫：断言失败也要 join B——否则其异步提交会撞下一测试的 TRUNCATE。
        if thread is not None and thread.is_alive():
            thread.join(timeout=30)
    assert isinstance(outcome.get("error"), ValueError), outcome
    assert "collision" in str(outcome["error"]).lower()
    assert not storage_dir.exists(), "checkpoint (2) must reject before any mkdir"

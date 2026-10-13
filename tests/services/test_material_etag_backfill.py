"""#1279 codex r8/r9：存量 ready 行 object_etag 启动回填（binder）。

格子：静止期守卫（首见标记 + 满 presign TTL 才绑定）、缺对象翻 failed
（候选集自净）、CAS 竞争让位、幂等、存储故障零绑定 done=False、游标
活性（永久失败行压头不挡后续）、sweeper 线程接线（启动即跑 + 间隔重试
+ 无存储短路）。
"""

from __future__ import annotations

import time

from botocore.exceptions import ClientError

import server.app.services.material_etag_backfill as backfill_module
from server.app.services.material_etag_backfill import backfill_legacy_material_etags
from server.app.services.material_ttl_sweeper import MaterialTtlSweeperThread
from tests.fakes.storage import etag_of
from tests.services import materials_testlib

WORKSPACE_ID = materials_testlib.WORKSPACE_ID
storage = materials_testlib.storage
service = materials_testlib.service
_object_etag_of = materials_testlib.object_etag_of

PAYLOAD = b"legacy-row-bytes"


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _legacy_ready_row(job_db, service, storage, payload: bytes = PAYLOAD) -> tuple[str, str]:
    """一条 ready 但 object_etag='' 的存量行（模拟 v98 前完成上传的行）。"""
    ready = materials_testlib.presign_ready(service, storage, payload)
    material_id = ready["id"]
    with job_db.connect() as conn:
        conn.execute("update materials set object_etag='' where id=%s", (material_id,))
    return material_id, materials_testlib.storage_key_of(job_db, material_id)


def test_backfill_binds_only_after_quiescent_window(service, storage, job_db) -> None:
    """静止期守卫：首见只标记不绑定；身份标记（key+代次+etag）持续满一个
    presign TTL 才落库。"""
    material_id, _key = _legacy_ready_row(job_db, service, storage)
    clock = _Clock()
    pending: dict = {}

    first = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    assert first["bound"] == 0 and first["pending"] == 1 and first["done"] is False
    assert _object_etag_of(job_db, material_id) == "", "静止期未满不得绑定"

    # 同一身份第二次见到但未满 TTL：仍不得绑定（静止期守卫的实质格）。
    clock.t += 10
    early = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    assert early["bound"] == 0 and early["pending"] == 1
    assert _object_etag_of(job_db, material_id) == ""

    clock.t += backfill_module._PRESIGN_EXPIRY_SECONDS + 1
    second = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    assert second == {
        "bound": 1,
        "marked_failed": 0,
        "cas_lost": 0,
        "failed": 0,
        "pending": 0,
        "done": True,
    }
    assert _object_etag_of(job_db, material_id) == etag_of(PAYLOAD)
    # 幂等：重跑无可回填。
    third = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    assert third["done"] and third["bound"] == 0


def test_quiescence_timer_resets_when_identity_changes(service, storage, job_db) -> None:
    """标记期内对象被活 URL 改写（etag 变了）→ 重新计时，不绑旧身份。"""
    material_id, key = _legacy_ready_row(job_db, service, storage)
    clock = _Clock()
    pending: dict = {}
    backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    # 窗口内对象被同尺寸覆盖（活 URL 的幽灵写）。
    storage.objects[key] = b"legacy-row-XXXXX"
    clock.t += backfill_module._PRESIGN_EXPIRY_SECONDS + 1
    stats = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    assert stats["bound"] == 0 and stats["pending"] == 1
    assert _object_etag_of(job_db, material_id) == ""
    # 再满一个 TTL（身份未再变）→ 绑定新 etag。
    clock.t += backfill_module._PRESIGN_EXPIRY_SECONDS + 1
    stats = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    assert stats["bound"] == 1
    assert _object_etag_of(job_db, material_id) == etag_of(b"legacy-row-XXXXX")


def test_backfill_marks_missing_object_failed(service, storage, job_db) -> None:
    """缺对象行直接翻 failed（complete 同语义，repair 可救），候选集自净。"""
    material_id, key = _legacy_ready_row(job_db, service, storage)
    del storage.objects[key]

    stats = backfill_legacy_material_etags(job_db.dsn_identity, storage, pause_seconds=0)

    assert stats["marked_failed"] == 1 and stats["bound"] == 0 and stats["done"] is True
    assert service.get(WORKSPACE_ID, material_id)["status"] == "failed"
    assert _object_etag_of(job_db, material_id) == ""


def test_permanently_missing_rows_do_not_starve_later_candidates(service, storage, job_db) -> None:
    """扫描活性（r9 P1-3）：205 个缺对象行压头 + 3 个正常行——游标越过
    失败项，正常行照样标记/可绑，缺对象行翻 failed 后退出候选集。"""
    with job_db.connect() as conn:
        for index in range(205):
            conn.execute(
                "insert into materials(id, workspace_id, content_hash, filename, content_type,"
                " size_bytes, storage_key, status, created_by, object_etag, created_at)"
                " values (%s, %s, '', 'm.txt', 'text/plain', 1, %s, 'ready', 'user-1', '',"
                " now() - make_interval(days => 1))",
                (f"mat-missing-{index}", WORKSPACE_ID, f"{WORKSPACE_ID}/missing-{index}/f"),
            )
    good_ids = [
        _legacy_ready_row(job_db, service, storage, f"good-{i}".encode())[0] for i in range(3)
    ]
    clock = _Clock()
    pending: dict = {}

    first = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, batch_size=50, pause_seconds=0, pending=pending, now=clock
    )
    assert first["marked_failed"] == 205, "缺对象行全部翻 failed（不挡扫描）"
    assert first["pending"] == 3, "正常行照样进入静止期标记"
    clock.t += backfill_module._PRESIGN_EXPIRY_SECONDS + 1
    second = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, batch_size=50, pause_seconds=0, pending=pending, now=clock
    )
    assert second["bound"] == 3 and second["done"] is True
    for material_id in good_ids:
        assert _object_etag_of(job_db, material_id) != ""
    # 翻 failed 的行已退出候选集：第三轮 done 且零动作。
    third = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, batch_size=50, pause_seconds=0, pending=pending, now=clock
    )
    assert third["done"] is True and third["marked_failed"] == 0


def test_backfill_yields_on_cas_race(service, storage, job_db, monkeypatch) -> None:
    """HEAD 之后、CAS 之前行被并发改动（complete/repair 先动）→ 让位，
    不用 HEAD 到的旧值覆盖。"""
    material_id, _key = _legacy_ready_row(job_db, service, storage)
    clock = _Clock()
    pending: dict = {}
    backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    clock.t += backfill_module._PRESIGN_EXPIRY_SECONDS + 1
    real_head = storage.head_object

    def head_then_race(storage_key: str):
        head = real_head(storage_key)
        with job_db.connect() as conn:
            # 并发 repair 的落点形态：etag 已先被改成别的值。
            conn.execute("update materials set object_etag='raced' where id=%s", (material_id,))
        return head

    monkeypatch.setattr(storage, "head_object", head_then_race)
    stats = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )

    assert stats["cas_lost"] == 1 and stats["bound"] == 0
    assert _object_etag_of(job_db, material_id) == "raced"


def test_backfill_yields_when_repair_rotated_key(service, storage, job_db, monkeypatch) -> None:
    """CAS 的 storage_key 钉：HEAD 之后并发 repair 清了 etag 且轮换了 key
    （etag 仍是 ''，单 etag 钉会被骗过），带旧 key 的 CAS 必须不落库。"""
    material_id, key = _legacy_ready_row(job_db, service, storage)
    clock = _Clock()
    pending: dict = {}
    backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    clock.t += backfill_module._PRESIGN_EXPIRY_SECONDS + 1
    real_head = storage.head_object

    def head_then_repair(storage_key: str):
        head = real_head(storage_key)
        # 并发 repair：清 etag + 轮换 key + 翻 uploading（object_etag 仍是 ''）。
        service.request_material_reupload(WORKSPACE_ID, material_id)
        return head

    monkeypatch.setattr(storage, "head_object", head_then_repair)
    stats = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )

    assert stats["cas_lost"] == 1 and stats["bound"] == 0
    assert _object_etag_of(job_db, material_id) == ""
    assert materials_testlib.storage_key_of(job_db, material_id) != key


def test_backfill_storage_outage_is_not_done(service, storage, job_db, monkeypatch) -> None:
    """存储面故障：HEAD 失败 → 零绑定、done=False（等下一轮），不动行。"""
    material_id, _key = _legacy_ready_row(job_db, service, storage)

    def failing_head(_key: str):
        raise ClientError({"Error": {"Code": "InternalError"}}, "HeadObject")

    monkeypatch.setattr(storage, "head_object", failing_head)
    stats = backfill_legacy_material_etags(job_db.dsn_identity, storage, pause_seconds=0)

    assert stats["failed"] == 1 and stats["done"] is False and stats["bound"] == 0
    assert _object_etag_of(job_db, material_id) == ""


def test_sweeper_thread_backfills_at_startup_and_retries(
    service, storage, job_db, monkeypatch
) -> None:
    """接线格：sweeper 线程启动即跑回填（不等首个 sweep 间隔）；静止期
    计时满后（此处 TTL 打 0）间隔重试轮完成绑定。"""
    monkeypatch.setattr(backfill_module, "_PRESIGN_EXPIRY_SECONDS", 0)
    material_id, _key = _legacy_ready_row(job_db, service, storage)
    thread = MaterialTtlSweeperThread(job_db.dsn_identity, storage, interval_seconds=0.05)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _object_etag_of(job_db, material_id) == etag_of(PAYLOAD) and thread._backfill_done:
                break
            time.sleep(0.02)
        assert _object_etag_of(job_db, material_id) == etag_of(PAYLOAD)
        assert thread._backfill_done
    finally:
        thread.stop()


def test_sweeper_backfill_stats_path_tolerates_marked_failed(service, storage, job_db) -> None:
    """评审 P2-①：binder 结果含 marked_failed（缺对象翻 failed）时 sweeper
    统计路径不抛 KeyError（改名 missing→marked_failed 的 callsite 回归）。"""
    material_id, key = _legacy_ready_row(job_db, service, storage)
    del storage.objects[key]
    thread = MaterialTtlSweeperThread(job_db.dsn_identity, storage, interval_seconds=3600)

    thread._run_backfill()  # 不抛即过（旧代码 KeyError: 'missing'）

    assert service.get(WORKSPACE_ID, material_id)["status"] == "failed"


def test_done_not_wedged_by_stale_pending_mark(service, storage, job_db) -> None:
    """评审 P2-②：行带着 pending 标记离开候选集（被 repair 走）后，done
    按本轮候选 pending 计数判定，不被陈旧标记永久楔形。"""
    material_id, _key = _legacy_ready_row(job_db, service, storage)
    clock = _Clock()
    pending: dict = {}
    first = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    assert first["done"] is False and pending  # 标记在、未绑

    # 行被 repair 走（uploading，离开候选集），标记残留在 map 里。
    service.request_material_reupload(WORKSPACE_ID, material_id)
    assert pending, "repair 不清 binder 的 map（由身份比对兜住安全）"

    second = backfill_legacy_material_etags(
        job_db.dsn_identity, storage, pause_seconds=0, pending=pending, now=clock
    )
    assert second["done"] is True, "候选集已空：陈旧标记不得楔形 done"


def test_sweeper_thread_backfill_skips_without_storage(job_db) -> None:
    """无对象存储 = 无材料特性：回填直接标记完成，不炸线程。"""
    thread = MaterialTtlSweeperThread(job_db.dsn_identity, None, interval_seconds=3600)
    thread._run_backfill()
    assert thread._backfill_done

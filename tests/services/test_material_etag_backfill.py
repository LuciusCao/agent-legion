"""#1279 codex r8：存量 ready 行 object_etag 启动回填（material_etag_backfill
+ MaterialTtlSweeperThread 接线）。

覆盖：正常回填（''→HEAD etag 落库）、对象缺失跳过并计数、CAS 竞争让位
（期间被改动则不覆盖）、幂等重跑、零进展闸（存储故障 → done=False 等下
一轮）、sweeper 线程启动即跑与无存储短路。
"""

from __future__ import annotations

import time

from botocore.exceptions import ClientError

from server.app.services.material_etag_backfill import backfill_legacy_material_etags
from server.app.services.material_ttl_sweeper import MaterialTtlSweeperThread
from tests.fakes.storage import etag_of
from tests.services import materials_testlib

WORKSPACE_ID = materials_testlib.WORKSPACE_ID
storage = materials_testlib.storage
service = materials_testlib.service
_object_etag_of = materials_testlib.object_etag_of

PAYLOAD = b"legacy-row-bytes"


def _legacy_ready_row(job_db, service, storage) -> tuple[str, str]:
    """一条 ready 但 object_etag='' 的存量行（模拟 v98 前完成上传的行）。"""
    ready = materials_testlib.presign_ready(service, storage, PAYLOAD)
    material_id = ready["id"]
    with job_db.connect() as conn:
        conn.execute("update materials set object_etag='' where id=%s", (material_id,))
    return material_id, materials_testlib.storage_key_of(job_db, material_id)


def test_backfill_binds_head_etag(service, storage, job_db) -> None:
    material_id, _key = _legacy_ready_row(job_db, service, storage)

    stats = backfill_legacy_material_etags(job_db.dsn_identity, storage, pause_seconds=0)

    assert stats == {"bound": 1, "missing": 0, "cas_lost": 0, "failed": 0, "done": True}
    assert _object_etag_of(job_db, material_id) == etag_of(PAYLOAD)
    # 幂等：重跑无可回填（done，零绑定）。
    again = backfill_legacy_material_etags(job_db.dsn_identity, storage, pause_seconds=0)
    assert again["done"] and again["bound"] == 0


def test_backfill_skips_missing_object(service, storage, job_db) -> None:
    material_id, key = _legacy_ready_row(job_db, service, storage)
    del storage.objects[key]

    stats = backfill_legacy_material_etags(job_db.dsn_identity, storage, pause_seconds=0)

    assert stats["missing"] == 1 and stats["bound"] == 0
    assert _object_etag_of(job_db, material_id) == ""


def test_backfill_yields_on_cas_race(service, storage, job_db, monkeypatch) -> None:
    """HEAD 之后、CAS 之前行被并发改动（complete/repair 先动）→ 让位，
    不用 HEAD 到的旧值覆盖。"""
    material_id, _key = _legacy_ready_row(job_db, service, storage)
    real_head = storage.head_object

    def head_then_race(storage_key: str):
        head = real_head(storage_key)
        with job_db.connect() as conn:
            # 并发 repair 的落点形态：etag 已先被改成别的值。
            conn.execute("update materials set object_etag='raced' where id=%s", (material_id,))
        return head

    monkeypatch.setattr(storage, "head_object", head_then_race)
    stats = backfill_legacy_material_etags(job_db.dsn_identity, storage, pause_seconds=0)

    assert stats["cas_lost"] == 1 and stats["bound"] == 0
    assert _object_etag_of(job_db, material_id) == "raced"


def test_backfill_zero_progress_is_not_done(service, storage, job_db, monkeypatch) -> None:
    """存储面故障：每行 HEAD 失败 → 零进展闸收尾，done=False（等下一轮）。"""
    _legacy_ready_row(job_db, service, storage)

    def failing_head(_key: str):
        raise ClientError({"Error": {"Code": "InternalError"}}, "HeadObject")

    monkeypatch.setattr(storage, "head_object", failing_head)
    stats = backfill_legacy_material_etags(job_db.dsn_identity, storage, pause_seconds=0)

    assert stats["failed"] == 1 and stats["done"] is False


def test_sweeper_thread_backfills_at_startup(service, storage, job_db) -> None:
    """接线格：sweeper 线程启动即跑回填（不等首个 sweep 间隔）。"""
    material_id, _key = _legacy_ready_row(job_db, service, storage)
    thread = MaterialTtlSweeperThread(job_db.dsn_identity, storage, interval_seconds=3600)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _object_etag_of(job_db, material_id) == etag_of(PAYLOAD):
                break
            time.sleep(0.05)
        assert _object_etag_of(job_db, material_id) == etag_of(PAYLOAD)
    finally:
        thread.stop()


def test_sweeper_thread_backfill_skips_without_storage(job_db) -> None:
    """无对象存储 = 无材料特性：回填直接标记完成，不炸线程。"""
    thread = MaterialTtlSweeperThread(job_db.dsn_identity, None, interval_seconds=3600)
    thread._run_backfill()
    assert thread._backfill_done

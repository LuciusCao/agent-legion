"""#1316：存量无 hash 材料 ETag 绑定（写端 + 驱动方）。

决策表（materials-and-runs-design §6.4.1 #1316）的绑定格与驱动方格：

- 候选只含 ready ∧ 无 hash ∧ etag=''；hash / uploading / 已绑行不动（P4）；
- 失败只报告、不改行（缺对象 / size 不符 / 空 ETag / HEAD 抛错），行保持
  etag=''（size-only），绝不翻 failed（P4）；
- CAS 钉 key + 代次 + status：扫描后被 repair 的行让位（P5 的同族要求，
  rebind 的 CAS 不在此）；
- keyset：前排永久失败行不挡后续行；
- 驱动方：线程启动即扫、暂时性失败有界重扫、新实例（= 重启）续上（P3/P7）。
"""

from __future__ import annotations

import pytest

from server.app.jobs.queries.material_legacy_etag import count_legacy_etag_bind_candidates
from server.app.services.material_legacy_etag_binding import (
    LegacyMaterialEtagBinderThread,
    bind_legacy_material_etags,
    start_legacy_material_etag_binder,
)
from tests.fakes.storage import etag_of
from tests.services import materials_testlib

WORKSPACE_ID = materials_testlib.WORKSPACE_ID
storage = materials_testlib.storage
service = materials_testlib.service
legacy_hashless = materials_testlib.legacy_hashless


def _row(job_db, material_id: str) -> dict:
    with job_db.connect() as conn:
        row = conn.execute(
            "select status, object_etag, storage_key from materials where id=%s",
            (material_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def test_binds_legacy_hashless_rows_with_current_head(job_db, service, storage) -> None:
    material_id = legacy_hashless(job_db, service, storage, b"legacy-bytes")

    report = bind_legacy_material_etags(job_db, storage)

    assert report.bound == [material_id]
    row = _row(job_db, material_id)
    assert row["status"] == "ready"
    assert row["object_etag"] == etag_of(b"legacy-bytes")
    assert count_legacy_etag_bind_candidates(job_db) == 0


def test_candidates_exclude_hash_uploading_and_bound_rows(job_db, service, storage) -> None:
    """P4：hash 行（sha256 兜底）、uploading 行（complete 时落 etag）、已绑行不碰。"""
    hashed = materials_testlib.presign_ready(service, storage, b"hashed")
    with job_db.connect() as conn:
        conn.execute("update materials set object_etag='' where id=%s", (hashed["id"],))
    uploading = service.presign(WORKSPACE_ID, filename="u.txt", size_bytes=3)["material"]["id"]
    bound = legacy_hashless(job_db, service, storage, b"bound", filename="b.txt")
    with job_db.connect() as conn:
        conn.execute("update materials set object_etag='etag-x' where id=%s", (bound,))

    report = bind_legacy_material_etags(job_db, storage)

    assert report.bound == []
    assert _row(job_db, hashed["id"])["object_etag"] == ""
    assert _row(job_db, uploading)["status"] == "uploading"
    assert _row(job_db, bound)["object_etag"] == "etag-x"


@pytest.mark.parametrize("failure", ["missing_object", "size_mismatch", "empty_etag", "head_error"])
def test_failures_only_report_and_never_touch_the_row(
    job_db, service, storage, monkeypatch, failure
) -> None:
    """P4：绑定失败只报告——行保持 ready ∧ etag=''（size-only），绝不翻 failed。"""
    material_id = legacy_hashless(job_db, service, storage, b"payload")
    key = _row(job_db, material_id)["storage_key"]
    if failure == "missing_object":
        del storage.objects[key]
    elif failure == "size_mismatch":
        storage.objects[key] = b"longer-payload"
    elif failure == "empty_etag":
        storage.no_etag = True
    else:

        def _boom(storage_key: str):
            raise ConnectionError("endpoint unreachable")

        monkeypatch.setattr(storage, "head_object", _boom)

    report = bind_legacy_material_etags(job_db, storage)

    expected_bucket = {
        "missing_object": report.missing_object,
        "size_mismatch": report.size_mismatch,
        "empty_etag": report.empty_etag,
        "head_error": report.head_errors,
    }[failure]
    assert expected_bucket == [material_id]
    assert report.bound == []
    row = _row(job_db, material_id)
    assert row["status"] == "ready"
    assert row["object_etag"] == ""


def test_cas_yields_to_a_repair_between_scan_and_bind(job_db, service, storage, monkeypatch):
    """扫描后、绑定前 repair 轮换了 key：CAS 让位，不把旧 key 的 HEAD 绑到新身份。"""
    material_id = legacy_hashless(job_db, service, storage, b"repair-race")
    real_head = storage.head_object

    def _head_then_repair(storage_key: str):
        head = real_head(storage_key)
        service.request_material_reupload(WORKSPACE_ID, material_id)
        return head

    monkeypatch.setattr(storage, "head_object", _head_then_repair)

    report = bind_legacy_material_etags(job_db, storage)

    assert report.cas_lost == [material_id]
    row = _row(job_db, material_id)
    assert row["status"] == "uploading"
    assert row["object_etag"] == ""


def test_keyset_scan_skips_past_permanent_failures(job_db, service, storage) -> None:
    """前排永久失败行（缺对象）不挡住后续行：小批量也能扫完。"""
    broken = [
        legacy_hashless(job_db, service, storage, b"x" * (i + 1), filename=f"x{i}.txt")
        for i in range(3)
    ]
    for material_id in broken:
        del storage.objects[_row(job_db, material_id)["storage_key"]]
    healthy = legacy_hashless(job_db, service, storage, b"healthy", filename="h.txt")

    report = bind_legacy_material_etags(job_db, storage, batch_size=2)

    assert report.bound == [healthy]
    assert sorted(report.missing_object) == sorted(broken)


def test_dry_run_reports_without_writing(job_db, service, storage) -> None:
    material_id = legacy_hashless(job_db, service, storage, b"dry")

    report = bind_legacy_material_etags(job_db, storage, apply=False)

    assert report.bound == [material_id]
    assert _row(job_db, material_id)["object_etag"] == ""


# --- 驱动方 ------------------------------------------------------------------


def test_thread_binds_and_finishes(job_db, service, storage) -> None:
    material_id = legacy_hashless(job_db, service, storage, b"thread")
    thread = LegacyMaterialEtagBinderThread(job_db, storage, retry_interval_seconds=0)

    thread.run()

    assert thread.done and thread.passes == 1
    assert _row(job_db, material_id)["object_etag"] == etag_of(b"thread")


def test_thread_retries_transient_failures_boundedly_and_restart_resumes(
    job_db, service, storage, monkeypatch
) -> None:
    """P3/P7：存储故障时有界重扫后放弃（行保持 size-only，不挂起不失败）；
    下一次启动（新线程实例）从 DB 现读候选，续上绑定——进度不在内存里。"""
    material_id = legacy_hashless(job_db, service, storage, b"flaky")
    real_head = storage.head_object

    def _down(storage_key: str):
        raise ConnectionError("storage down")

    monkeypatch.setattr(storage, "head_object", _down)
    first = LegacyMaterialEtagBinderThread(job_db, storage, retry_interval_seconds=0, max_passes=3)
    first.run()
    assert not first.done and first.passes == 3
    assert _row(job_db, material_id)["object_etag"] == ""

    monkeypatch.setattr(storage, "head_object", real_head)
    restarted = LegacyMaterialEtagBinderThread(job_db, storage, retry_interval_seconds=0)
    restarted.run()
    assert restarted.done
    assert _row(job_db, material_id)["object_etag"] == etag_of(b"flaky")


def test_permanent_failures_do_not_cause_retries(job_db, service, storage) -> None:
    """缺对象是永久问题：只报告，一轮即退（不把永久失败当暂时态反复扫）。"""
    material_id = legacy_hashless(job_db, service, storage, b"gone")
    del storage.objects[_row(job_db, material_id)["storage_key"]]
    thread = LegacyMaterialEtagBinderThread(job_db, storage, retry_interval_seconds=0)

    thread.run()

    assert thread.done and thread.passes == 1


def test_no_thread_without_object_storage(job_db) -> None:
    assert start_legacy_material_etag_binder(job_db, None) is None

"""scripts/rebind_material_etags.py 的核心判定（#1279 codex r4 P1 恢复面）：
重绑/拒绑两路——ETag 一致不动、失配但字节核验通过才重绑（dry-run 只报告、
--apply 经四钉 CAS 落库）、字节不符或对象缺失 fail-closed、CAS 落空按竞态
失败单列报告（#1313）且退出码非零。真实 DB 上的并发写者 × 四钉见
tests/services/test_material_rebind_races.py。

直测 ``rebind_material_etags`` 函数级行为：FakeObjectStorage 提供对象与
MD5 形态的 ETag，_FakeQueries 提供内存行与 CAS 语义。CLI 薄壳（参数/
dotenv/真实接线）同 gc-s3-jobs 惯例不在这里重放。
"""

from __future__ import annotations

import hashlib

import pytest

from scripts.rebind_material_etags import RebindReport, print_report, rebind_material_etags
from tests.fakes.storage import FakeObjectStorage, etag_of

pytestmark = pytest.mark.no_db

PAYLOAD = b"restored-bytes"
HASH = hashlib.sha256(PAYLOAD).hexdigest()
KEY = "ws-1/" + HASH + "/v2g0/m_" + "a" * 32 + "/doc.txt"


class _FakeQueries:
    """内存行 + CAS 语义的重绑门面；``cas_fail`` 模拟并发改动。"""

    def __init__(self, rows: list[dict], cas_fail: bool = False) -> None:
        self.rows = rows
        self.cas_fail = cas_fail
        self.rebound: list[tuple[str, str, str]] = []
        self.pins: list[tuple[str, int, str]] = []

    def materials_with_object_etag(self) -> list[dict]:
        return [dict(row) for row in self.rows]

    def rebind_material_etag(
        self,
        material_id: str,
        *,
        expected_etag: str,
        new_etag: str,
        storage_key: str,
        upload_generation: int,
        status: str,
    ) -> bool:
        self.pins.append((storage_key, upload_generation, status))
        if self.cas_fail:
            return False
        self.rebound.append((material_id, expected_etag, new_etag))
        return True


def _row(etag: str, *, content_hash: str = HASH, size: int = len(PAYLOAD)) -> dict:
    return {
        "id": "mat-1",
        "workspace_id": "ws-1",
        "content_hash": content_hash,
        "size_bytes": size,
        "storage_key": KEY,
        "object_etag": etag,
        "upload_generation": 3,
        "status": "ready",
    }


def test_matching_etag_is_untouched() -> None:
    storage = FakeObjectStorage(objects={KEY: PAYLOAD})
    report = rebind_material_etags(storage, _FakeQueries([_row(etag_of(PAYLOAD))]), apply=True)
    assert report.matched == 1 and report.rebound == [] and report.unresolved == []


def test_mismatched_etag_rebinds_after_byte_verification() -> None:
    """恢复后 ETag 变化（AWS CLI 分段上传）但字节一致 → 重绑为新 ETag。"""
    storage = FakeObjectStorage(objects={KEY: PAYLOAD})
    queries = _FakeQueries([_row("stale-backup-etag")])

    dry = rebind_material_etags(storage, queries, apply=False)
    assert dry.rebound == ["mat-1"] and dry.unresolved == []
    assert queries.rebound == [], "dry-run 不落库"

    applied = rebind_material_etags(storage, queries, apply=True)
    assert applied.rebound == ["mat-1"]
    assert queries.rebound == [("mat-1", "stale-backup-etag", etag_of(PAYLOAD))]
    # 四钉原样取自扫描快照（#1313）。
    assert queries.pins == [(KEY, 3, "ready")]
    assert print_report(applied, apply=True) == 0


def test_bad_bytes_or_missing_object_fail_closed() -> None:
    """字节核验失败（size/sha256 不符）或对象缺失 → 拒绑，只在报告里。"""
    storage = FakeObjectStorage(objects={KEY: b"different-rest"})  # 同长异内容
    queries = _FakeQueries([_row("stale")])
    report = rebind_material_etags(storage, queries, apply=True)
    assert report.rebound == [] and queries.rebound == []
    assert len(report.unresolved) == 1 and "sha256" in report.unresolved[0]

    storage_missing = FakeObjectStorage()
    report_missing = rebind_material_etags(
        storage_missing, _FakeQueries([_row("stale")]), apply=True
    )
    assert report_missing.rebound == [] and "missing" in report_missing.unresolved[0]


def test_cas_loss_is_reported_as_race_with_nonzero_exit(capsys) -> None:
    storage = FakeObjectStorage(objects={KEY: PAYLOAD})
    queries = _FakeQueries([_row("stale")], cas_fail=True)
    report = rebind_material_etags(storage, queries, apply=True)
    assert report.rebound == [] and report.unresolved == []
    assert len(report.raced) == 1 and "CAS lost" in report.raced[0]
    assert report.failed
    assert print_report(report, apply=True) == 1
    assert "竞态失败 1 行" in capsys.readouterr().out


def test_exit_code_is_nonzero_when_any_row_fails() -> None:
    """unresolved 与 raced 任一非空 → 1；两类同时出现也都要打印、不短路。"""
    assert print_report(RebindReport(matched=2, rebound=["a"]), apply=True) == 0
    assert print_report(RebindReport(unresolved=["a: missing"]), apply=True) == 1
    assert print_report(RebindReport(raced=["b: CAS lost"]), apply=False) == 1
    assert print_report(RebindReport(unresolved=["a: x"], raced=["b: y"]), apply=True) == 1

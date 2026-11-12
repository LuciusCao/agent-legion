"""Unit tests for server/app/services/s3_material_gc.py（#1314 材料删除批）。

判定与删除只依赖注入的列举器、key 存在性函数与批删客户端；与写端协议的
交错（真实 Postgres）见 tests/services/test_material_gc_write_protocol.py。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.app.services import s3_jobs_gc, s3_material_gc
from server.app.services.s3_jobs_gc import ObjectEntry

pytestmark = pytest.mark.no_db

NOW = datetime.now(UTC)
_MID = "m_" + "e" * 32
_NEW = f"ws1/{'a' * 64}/v2g0/{_MID}/doc.txt"  # 单次使用文法
_LEGACY = f"ws1/{'a' * 64}/g0/doc.txt"  # 旧文法（可复用）


def _entry(key: str, *, hours_old: float = 48.0) -> ObjectEntry:
    return ObjectEntry(key, NOW - timedelta(hours=hours_old), 10)


class _RecordingDeleter:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_objects(self, Bucket, Delete):  # noqa: ANN001, N803 - boto3 shape
        self.deleted.extend(o["Key"] for o in Delete["Objects"])
        return {"Errors": []}


@pytest.mark.parametrize(
    ("key", "hours_old", "grace_hours", "lane"),
    [
        (_NEW, 48.0, 24.0, "deletable"),
        (_NEW, 2.0, 0.0, "deletable"),  # 过 1h 下限
        (_NEW, 0.5, 0.0, "deferred"),  # --grace-hours 0 压不穿下限
        (_NEW, 2.0, 24.0, "deferred"),  # --grace-hours 大于下限时取大
        (_LEGACY, 48.0, 0.0, "report_only"),
        (f"ws1/{'a' * 64}/g0/{_MID}/doc.txt", 48.0, 0.0, "report_only"),  # r2 反例
        (f"ws1/{'a' * 64}/inline-{'d' * 32}", 48.0, 0.0, "report_only"),  # 旧 inline
        (f"jobs/{'a' * 64}/v2g3/{_MID}/x.bin", 48.0, 0.0, "deletable"),  # 撞名前缀同门禁
    ],
)
def test_partition_material_orphans_table(key, hours_old, grace_hours, lane) -> None:
    """候选三分：文法门 × 年龄下限 × --grace-hours。"""
    split = s3_material_gc.partition_material_orphans(
        [_entry(key, hours_old=hours_old)], grace_hours=grace_hours, now=NOW
    )
    lanes = {name: [e.key for e in getattr(split, name)] for name in vars(split)}
    assert lanes == {name: ([key] if name == lane else []) for name in lanes}


@pytest.mark.parametrize(
    ("key", "hours_old", "revalidate", "deleted", "withheld", "skipped"),
    [
        (_NEW, 2.0, "empty", True, 0, 0),
        (_NEW, 2.0, "has_row", False, 0, 1),  # 反查救下
        (_NEW, 2.0, None, False, 1, 0),  # 无反查：整道不删
        (_NEW, 0.5, "empty", False, 1, 0),  # 删除动作处复核年龄下限
        (_LEGACY, 48.0, "empty", False, 1, 0),  # 删除动作处复核文法门
    ],
)
def test_apply_material_gc_gates(key, hours_old, revalidate, deleted, withheld, skipped):
    """门禁钉在删除动作本身——手工构造的列表同样受约束。"""
    deleter = _RecordingDeleter()
    revalidate_fn = {
        None: None,
        "empty": lambda keys: set(),
        "has_row": lambda keys: set(keys),
    }[revalidate]

    result = s3_material_gc.apply_material_gc(
        deleter, "bkt", [_entry(key, hours_old=hours_old)], revalidate_fn
    )

    assert deleter.deleted == ([key] if deleted else [])
    assert (result.deleted, result.withheld, result.skipped_revalidated) == (
        int(deleted),
        withheld,
        skipped,
    )


def test_apply_material_gc_age_floor_uses_delete_time_clock() -> None:
    """年龄下限按删除时刻的时钟复核：clock 回拨到 PUT 后 30 分钟时扣下。"""
    deleter = _RecordingDeleter()
    entry = _entry(_NEW, hours_old=2.0)

    result = s3_material_gc.apply_material_gc(
        deleter,
        "bkt",
        [entry],
        lambda keys: set(),
        clock=lambda: entry.last_modified + timedelta(minutes=30),
    )

    assert deleter.deleted == []
    assert result.withheld == 1


def test_material_rollback_segment_keys_go_through_the_material_gates() -> None:
    """#1285/#1295：材料 key 的 /.rollback/ 段不构成豁免——根前缀与撞名前缀
    下的单次使用文法 key 照常经材料门禁删除。"""
    deleter = _RecordingDeleter()
    keys = [
        f"ws1/{'a' * 64}/v2g1/{_MID}/dir/.rollback/file.bin",
        f"jobs-staging/{'a' * 64}/v2g2/{_MID}/dir/.rollback/file.bin",
    ]

    result = s3_material_gc.apply_material_gc(
        deleter, "bkt", [_entry(k) for k in keys], lambda keys: set()
    )

    assert sorted(deleter.deleted) == sorted(keys)
    assert result.deleted == 2


def _lister(entries: list[ObjectEntry]):
    def list_prefix(prefix: str):
        yield from (e for e in entries if e.key.startswith(prefix))

    return list_prefix


def test_collect_gc_report_splits_collided_material_keys() -> None:
    """#1282 r4 P1：撞名 workspace（id 字面 jobs / jobs-staging）的材料形态
    key 从 jobs 两条道分流进材料候选（含 #1295 的 .rollback 段形态）；真
    job 产物（第 3 段 job id 恒含下划线）与 legacy 无代次段 key 留在 jobs
    删除批。根前缀扫描排除两个 jobs 前缀。"""
    entries = [
        _entry(f"jobs/{'a' * 64}/g0/doc.txt"),
        _entry(f"jobs/{'e' * 64}/v2g0/{_MID}/new.txt"),
        _entry(f"jobs-staging/{'d' * 64}/g2/dir/.rollback/file.bin"),
        _entry("jobs/ws-x/ws-x_q1/dead.json"),
        _entry(f"jobs/{'c' * 64}/old-name.txt"),
        _entry("jobs-staging/ws1/j1/e1/b.json"),
        _entry(_NEW),
        _entry(_LEGACY),
    ]

    report, split = s3_material_gc.collect_gc_report(
        _lister(entries), lambda keys: set(), grace_hours=24.0, staging_grace_hours=24.0
    )

    assert sorted(e.key for e in report.authority_orphans) == sorted(
        ["jobs/ws-x/ws-x_q1/dead.json", f"jobs/{'c' * 64}/old-name.txt"]
    )
    assert [e.key for e in report.staging_orphans] == ["jobs-staging/ws1/j1/e1/b.json"]
    assert sorted(e.key for e in split.deletable) == sorted(
        [_NEW, f"jobs/{'e' * 64}/v2g0/{_MID}/new.txt"]
    )
    assert sorted(e.key for e in split.report_only) == sorted(
        [
            _LEGACY,
            f"jobs/{'a' * 64}/g0/doc.txt",
            f"jobs-staging/{'d' * 64}/g2/dir/.rollback/file.bin",
        ]
    )


def test_collect_gc_report_longer_staging_window_does_not_delay_material_orphans() -> None:
    """#1322（#1318 codex r1 P2 的回归钉）：staging 宽限窗（168h）比材料
    宽限窗（24h）更长时，48h 的撞名 v2g 孤儿进删除批——不再被 staging
    截止点吞掉延迟回收；同龄的撞名旧文法 key 进只报告集合；同龄的真
    staging 对象仍在窗内（不进 jobs 报告也不进材料候选）；未满材料窗
    （12h）的撞名 v2g key 不进候选。"""
    v2g_key = f"jobs-staging/{'a' * 64}/v2g0/{_MID}/doc.txt"
    legacy_key = f"jobs-staging/{'b' * 64}/g0/doc.txt"
    young_v2g = f"jobs-staging/{'c' * 64}/v2g1/{_MID}/new.txt"
    true_staging = "jobs-staging/ws1/j1/e1/staging.bin"
    entries = [
        _entry(v2g_key, hours_old=48.0),
        _entry(legacy_key, hours_old=48.0),
        _entry(young_v2g, hours_old=12.0),
        _entry(true_staging, hours_old=48.0),
    ]

    report, split = s3_material_gc.collect_gc_report(
        _lister(entries), lambda keys: set(), grace_hours=24.0, staging_grace_hours=168.0
    )

    assert [e.key for e in split.deletable] == [v2g_key]
    assert [e.key for e in split.report_only] == [legacy_key]
    assert split.deferred == []
    assert report.staging_orphans == []
    assert report.authority_orphans == []
    assert report.count == 0


def test_material_root_scan_pages_key_existence(monkeypatch) -> None:
    """#1287：根前缀扫描按 KEY_PAGE_SIZE 分页判定——多页输入的孤儿归并正确，
    且 key_exists 按页分批调用（全量对象清单不驻留内存）。"""
    monkeypatch.setattr(s3_jobs_gc, "KEY_PAGE_SIZE", 10)
    entries = [_entry(f"ws1/hash-old/g0/f{i:04}.txt") for i in range(21)]
    entries.append(_entry("ws1/hash-live/g0/known.txt"))  # 有行：保留
    entries.append(_entry("ws1/hash-new/g0/fresh.txt", hours_old=0.01))  # 窗内：保留
    entries.append(_entry("jobs/ws1/j1/a.json"))  # jobs 前缀：不归根前缀扫描
    batches: list[int] = []

    def key_exists(keys: list[str]) -> set[str]:
        batches.append(len(keys))
        return {k for k in keys if k == "ws1/hash-live/g0/known.txt"}

    orphans = s3_material_gc.scan_material_root_orphans(_lister(entries), key_exists, 24.0)

    assert len(orphans) == 21
    assert batches == [10, 10, 3]

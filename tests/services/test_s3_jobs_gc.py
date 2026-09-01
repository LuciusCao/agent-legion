"""Unit tests for server/app/services/s3_jobs_gc.py.

判定核心只依赖注入的列举器与 key 存在性函数，直测函数级行为；批量边界
（KEY_PAGE_SIZE 分页判定）用恰好跨界规模的注入数据覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server.app.services import s3_jobs_gc
from server.app.services.s3_jobs_gc import ObjectEntry, scan_orphans


def _entry(key: str, *, hours_old: float = 48.0, size: int = 10) -> ObjectEntry:
    return ObjectEntry(
        key=key,
        last_modified=datetime.now(UTC) - timedelta(hours=hours_old),
        size_bytes=size,
    )


def _lister(entries_by_prefix: dict[str, list[ObjectEntry]]):
    def list_prefix(prefix: str):
        yield from entries_by_prefix.get(prefix, [])

    return list_prefix


NOW = datetime.now(UTC)


def test_authority_orphan_is_key_not_in_db() -> None:
    """jobs/ 下：DB 无行的 key 是孤儿；有行的不是。"""
    report = scan_orphans(
        _lister(
            {
                "jobs/": [
                    _entry("jobs/ws-1/job-1/out.json"),
                    _entry("jobs/ws-1/job-2/out.json"),
                ]
            }
        ),
        lambda keys: {"jobs/ws-1/job-1/out.json"},
        now=NOW,
    )
    assert [o.key for o in report.authority_orphans] == ["jobs/ws-1/job-2/out.json"]
    assert report.count == 1
    assert report.total_bytes == 10


def test_staging_orphans_only_past_grace() -> None:
    """jobs-staging/ 下：超宽限窗即回收候选（无 DB 对照）；窗内保留。"""
    report = scan_orphans(
        _lister(
            {
                "jobs-staging/": [
                    _entry("jobs-staging/ws-1/job-1/exec-1/a.json", hours_old=48),
                    _entry("jobs-staging/ws-1/job-1/exec-2/b.json", hours_old=1),
                ]
            }
        ),
        lambda keys: set(),
        now=NOW,
    )
    assert [o.key for o in report.staging_orphans] == ["jobs-staging/ws-1/job-1/exec-1/a.json"]
    assert report.authority_orphans == []


def test_authority_grace_window_keeps_recent_unknown_keys() -> None:
    """宽限窗内的未知 authority key 不是孤儿（上传在途/行未写场景）。"""
    report = scan_orphans(
        _lister({"jobs/": [_entry("jobs/ws-1/job-x/inflight.json", hours_old=1)]}),
        lambda keys: set(),
        now=NOW,
    )
    assert report.count == 0


def test_jobs_prefix_lister_receives_exact_prefix() -> None:
    """列举器收到精确前缀：jobs/ 与 jobs-staging/ 分两遍（前缀互不包含）。"""
    seen: list[str] = []

    def lister(prefix: str):
        seen.append(prefix)
        return iter([])

    scan_orphans(lister, lambda keys: set(), now=NOW)
    assert seen == ["jobs-staging/", "jobs/"]


def test_key_page_boundary_batching(monkeypatch) -> None:
    """跨 KEY_PAGE_SIZE 边界不漏键：分页只影响传给 key_exists 的批大小，
    判定结果与整批一次判定一致。"""
    monkeypatch.setattr(s3_jobs_gc, "KEY_PAGE_SIZE", 3)
    entries = [_entry(f"jobs/ws-1/job-{i}/a.json") for i in range(7)]
    queries: list[int] = []

    def key_exists(keys: list[str]) -> set[str]:
        queries.append(len(keys))
        # 每页留一个「DB 已知」键：i % 3 == 0。
        return {
            k
            for i, k in enumerate([e.key for e in entries])
            if k.endswith(f"job-{i}/a.json") and i % 3 == 0
        }

    report = scan_orphans(_lister({"jobs/": entries}), key_exists, now=NOW)
    # 7 键、页大小 3 → 调用 [3, 3, 1]；已知键 i%3==0 共 3 个 → 7-3=4 孤儿。
    assert queries == [3, 3, 1]
    assert report.count == 4
    known = {e.key for e in report.authority_orphans}
    assert "jobs/ws-1/job-0/a.json" not in known
    assert "jobs/ws-1/job-3/a.json" not in known
    assert "jobs/ws-1/job-6/a.json" not in known


def test_make_db_key_existence_loads_once() -> None:
    """全量载入只发生一次：首次调用触发 all_artifact_storage_keys，
    后续判定复用内存 set（无索引列上不能每批反查一次全表）。"""
    calls: list[int] = []

    class _Queries:
        def all_artifact_storage_keys(self) -> set[str]:
            calls.append(1)
            return {"jobs/ws-1/job-1/a.json", "jobs/ws-1/job-3/a.json"}

    exists = s3_jobs_gc.make_db_key_existence(_Queries())  # type: ignore[arg-type]
    first = exists(["jobs/ws-1/job-1/a.json", "jobs/ws-1/job-2/a.json"])
    second = exists(["jobs/ws-1/job-3/a.json", "jobs/ws-1/job-4/a.json"])
    assert calls == [1]
    assert first == {"jobs/ws-1/job-1/a.json"}
    assert second == {"jobs/ws-1/job-3/a.json"}

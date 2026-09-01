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
        def all_object_storage_keys(self) -> set[str]:
            calls.append(1)
            return {"jobs/ws-1/job-1/a.json", "jobs/ws-1/job-3/a.json"}

    exists = s3_jobs_gc.make_db_key_existence(_Queries())  # type: ignore[arg-type]
    first = exists(["jobs/ws-1/job-1/a.json", "jobs/ws-1/job-2/a.json"])
    second = exists(["jobs/ws-1/job-3/a.json", "jobs/ws-1/job-4/a.json"])
    assert calls == [1]
    assert first == {"jobs/ws-1/job-1/a.json"}
    assert second == {"jobs/ws-1/job-3/a.json"}


class _RecordingDeleter:
    """记下每批被删除 key 的批删双打。"""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_objects(self, Bucket, Delete):  # noqa: ANN001
        keys = [o["Key"] for o in Delete["Objects"]]
        self.deleted.extend(keys)
        return {"Errors": []}


def _report_with(authority: list[str], staging: list[str] = ()) -> s3_jobs_gc.OrphanReport:
    return s3_jobs_gc.OrphanReport(
        authority_orphans=[_entry(k) for k in authority],
        staging_orphans=[_entry(k) for k in staging],
    )


def test_apply_revalidate_skips_keys_that_gained_rows() -> None:
    """TOCTOU（#344 评审 P1）：扫描判孤儿后、删除前同名 key 被并发 promote
    重建行——删除批经新鲜反查过滤，不删新权威对象；被救下的计入
    skipped_revalidated。"""
    deleter = _RecordingDeleter()
    report = _report_with(
        authority=["jobs/ws-1/kept.json", "jobs/ws-1/gone.json"],
        staging=["jobs-staging/ws-1/job-1/exec-1/s.json"],
    )
    # 新鲜反查：kept.json 此刻已有 DB 行（并发 promote），gone.json 仍无。
    fresh = {"jobs/ws-1/kept.json"}

    result = s3_jobs_gc.apply_gc(
        deleter, "bkt", report, revalidate=lambda keys: {k for k in keys if k in fresh}
    )

    assert result.deleted == 2  # gone.json + staging
    assert result.skipped_revalidated == 1
    assert "jobs/ws-1/kept.json" not in deleter.deleted
    assert "jobs/ws-1/gone.json" in deleter.deleted
    assert "jobs-staging/ws-1/job-1/exec-1/s.json" in deleter.deleted


def test_apply_without_revalidate_deletes_all_reported() -> None:
    """未提供 revalidate（旧行为，纯函数调用方）：按报告全删。"""
    deleter = _RecordingDeleter()
    report = _report_with(authority=["jobs/ws-1/a.json"], staging=["jobs-staging/x/y.json"])

    result = s3_jobs_gc.apply_gc(deleter, "bkt", report)

    assert result.deleted == 2
    assert result.skipped_revalidated == 0
    assert sorted(deleter.deleted) == ["jobs-staging/x/y.json", "jobs/ws-1/a.json"]


def test_materials_of_workspace_named_jobs_is_not_orphan() -> None:
    """系统性评审 P1（#344）：materials key 是 ``{workspace_id}/{hash}/
    {filename}``，workspace id 允许叫 ``jobs`` / ``jobs-staging``——撞名
    workspace 的材料 key 落进 GC 两个前缀时，DB 对照（job_artifacts ∪
    materials 的 key 集合）必须把它们从孤儿判定中排除。"""
    material_keys = {
        "jobs/abc123/report.pdf",  # workspace 名 jobs 的材料,authority 前缀内
        "jobs-staging/def456/data.csv",  # workspace 名 jobs-staging 的材料,staging 前缀内
    }

    def key_exists(keys: list[str]) -> set[str]:
        return {k for k in keys if k in material_keys}

    report = scan_orphans(
        _lister(
            {
                "jobs/": [
                    _entry("jobs/abc123/report.pdf"),
                    _entry("jobs/ws-1/job-1/orphan.json"),
                ],
                "jobs-staging/": [
                    _entry("jobs-staging/def456/data.csv"),
                    _entry("jobs-staging/ws-1/job-1/exec-1/stale.json"),
                ],
            }
        ),
        key_exists,
        now=NOW,
    )

    assert [o.key for o in report.authority_orphans] == ["jobs/ws-1/job-1/orphan.json"]
    assert [o.key for o in report.staging_orphans] == ["jobs-staging/ws-1/job-1/exec-1/stale.json"]

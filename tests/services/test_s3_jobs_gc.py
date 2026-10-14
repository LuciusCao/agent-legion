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


def test_staging_orphans_exempt_rollback_backups() -> None:
    """codex #774 P1 族：``/.rollback/`` 段的 promote 回滚备份一律豁免回收
    ——恢复最终失败时被保留的备份是幸存清单行所指向旧字节的最后恢复源，
    其生命周期归运维处置；超窗、DB 无行都不构成删除理由。"""
    report = scan_orphans(
        _lister(
            {
                "jobs-staging/": [
                    _entry("jobs-staging/ws-1/job-1/exec-1/.rollback/att-1/out.json"),
                    _entry("jobs-staging/ws-1/job-1/exec-1/.rollback/att-1/out.json.gz"),
                    _entry("jobs-staging/ws-1/job-1/exec-1/plain-staging.json"),
                ]
            }
        ),
        lambda keys: set(),
        now=NOW,
    )
    assert [o.key for o in report.staging_orphans] == [
        "jobs-staging/ws-1/job-1/exec-1/plain-staging.json"
    ]


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


def test_apply_gc_never_deletes_rollback_backup_keys() -> None:
    """#774 对抗复审（纵深防御）：``/.rollback/`` 备份的豁免钉在删除动作本
    身——即使 OrphanReport 被手工构造（绕过 scan 层豁免），apply 也不删
    恢复失败时留存的最后恢复源。revalidate 救不了它们（回滚 key 永无 DB
    行），所以过滤必须先于反查。"""
    deleter = _RecordingDeleter()
    report = _report_with(
        authority=["jobs/ws-1/job-1/.rollback/x.json", "jobs/ws-1/job-1/gone.json"],
        staging=[
            "jobs-staging/ws-1/job-1/e1/.rollback/att-1/out.json",
            "jobs-staging/ws-1/job-1/e1/plain.json",
        ],
    )

    result = s3_jobs_gc.apply_gc(deleter, "bkt", report)  # 无 revalidate 的最坏路径

    assert sorted(deleter.deleted) == [
        "jobs-staging/ws-1/job-1/e1/plain.json",
        "jobs/ws-1/job-1/gone.json",
    ]
    assert result.deleted == 2


def test_rollback_exemption_scoped_to_jobs_prefixes() -> None:
    """#1285：``/.rollback/`` 豁免仅对 jobs/、jobs-staging/ 前缀的 key 生效
    ——promote 回滚备份只出现在这两个产物前缀下；材料根前缀 key 不适用
    （材料文件名允许合法多段相对路径、无 .rollback 保留段，
    dir/.rollback/file.bin 形态的旧代次/删行残留在删除批重开（#1314）后
    不得被豁免永久跳过）。钉豁免判定函数本身。"""
    assert s3_jobs_gc._rollback_backup_exempt("jobs/ws-1/job-1/.rollback/x.json")
    assert s3_jobs_gc._rollback_backup_exempt("jobs-staging/ws-1/job-1/e1/.rollback/a/o.json")
    assert not s3_jobs_gc._rollback_backup_exempt(f"ws1/{'a' * 64}/g1/dir/.rollback/file.bin")
    assert not s3_jobs_gc._rollback_backup_exempt(f"ws1/{'b' * 64}/g0/.rollback/file.bin")
    assert not s3_jobs_gc._rollback_backup_exempt("jobs/ws-1/job-1/plain.json")


def test_apply_gc_rollback_exemption_does_not_cover_material_root_keys() -> None:
    """#1285：apply_gc 的纵深防御豁免同样按前缀收窄——手工构造的
    OrphanReport 混入非材料形态的根前缀 key 时，其 /.rollback/ 段不构成
    豁免理由，jobs 前缀下的真回滚备份仍不可删。材料形态 key（#1314）
    一律不在 apply_gc 删除——只经 s3_material_gc 的门禁。"""
    deleter = _RecordingDeleter()
    root_key = f"ws1/{'a' * 64}/dir/.rollback/file.bin"
    material_key = f"ws1/{'a' * 64}/v2g1/m_{'e' * 32}/dir/.rollback/file.bin"
    report = _report_with(
        authority=["jobs/ws-1/job-1/.rollback/x.json", root_key, material_key],
    )

    result = s3_jobs_gc.apply_gc(deleter, "bkt", report)  # 无 revalidate 的最坏路径

    assert deleter.deleted == [root_key]
    assert result.deleted == 1


def test_material_shaped_key_grammar() -> None:
    """材料形态文法（#1282 r4 P1；自 scripts/gc-s3-jobs.py 下沉为
    s3_jobs_gc.material_shaped_key，#1295）：第 3 段是 g{代次}（#1267
    旧文法）或 v2g{代次}（#1279 现文法），两种都匹配——碰撞分流要收全
    形态，删除门禁在 material_key_single_use。真 job
    产物第 3 段是 job id（恒含下划线）永不匹配；legacy 无代次段材料
    key 与段数不足的 key 不匹配。"""
    assert s3_jobs_gc.material_shaped_key(f"ws1/{'a' * 64}/g0/doc.txt")
    assert s3_jobs_gc.material_shaped_key(f"ws1/{'a' * 64}/v2g0/m_{'e' * 32}/doc.txt")
    assert s3_jobs_gc.material_shaped_key(f"ws1/{'a' * 64}/g0/m_{'e' * 32}/doc.txt")
    assert s3_jobs_gc.material_shaped_key(f"jobs-staging/{'b' * 64}/g2/dir/.rollback/f.bin")
    assert not s3_jobs_gc.material_shaped_key("jobs/ws-x/ws-x_q1/dead.json")
    assert not s3_jobs_gc.material_shaped_key(f"jobs/{'c' * 64}/old-name.txt")
    assert not s3_jobs_gc.material_shaped_key("ws1/hash/g2")
    assert not s3_jobs_gc.material_shaped_key("ws1/hash/g2x/f.txt")


def test_material_key_single_use_grammar() -> None:
    """#1279 单次使用文法门禁（#1314 材料删除批的准入条件；不满足的材料
    孤儿只报告不删除）：第 3 段是 ``v2g{代次}``（写端不可注入的服务端派生段）
    且第 4 段是 ``m_`` 标记 + 32 位小写 hex material_id → 跨行构造性不可
    复用；其余形态一律否：旧文法 g 段 key（含 r1 反例「首文件名段恰为
    32 位 hex」与 r2 反例「首文件名段恰为 m_<32hex>」）、legacy 无 g 段
    key、inline 旧 key、真 job 产物。"""
    mid = "m_" + "a" * 32
    assert s3_jobs_gc.material_key_single_use(f"ws1/{'b' * 64}/v2g0/{mid}/doc.txt")
    assert s3_jobs_gc.material_key_single_use(f"ws1/{mid[2:]}/v2g2/{mid}/dir/f.bin")
    # 撞名前缀下的新文法材料 key 同门禁。
    assert s3_jobs_gc.material_key_single_use(f"jobs-staging/{'c' * 64}/v2g1/{mid}/f.bin")
    # 旧文法（g 段无 v2 标记）：可复用，排除。
    assert not s3_jobs_gc.material_key_single_use(f"ws1/{'b' * 64}/g0/doc.txt")
    # r1 反例：旧文法 + 首文件名段恰为 32 位 hex。
    assert not s3_jobs_gc.material_key_single_use(f"ws1/{'b' * 64}/g0/{'a' * 32}/doc.txt")
    # r2 反例：旧文法 + 首文件名段恰为 m_<32hex>。
    assert not s3_jobs_gc.material_key_single_use(f"ws1/{'b' * 64}/g0/{mid}/doc.txt")
    # legacy 无 g 段 / inline 尾段（旧文法）。
    assert not s3_jobs_gc.material_key_single_use(f"ws1/{'b' * 64}/doc.txt")
    assert not s3_jobs_gc.material_key_single_use(f"ws1/{'b' * 64}/inline-{'d' * 32}")
    # 第 4 段不是 m_ + 32 位 hex / 代次段缺数字的形态。
    assert not s3_jobs_gc.material_key_single_use(f"ws1/{'b' * 64}/v2g0/notanid/doc.txt")
    assert not s3_jobs_gc.material_key_single_use(f"ws1/{'b' * 64}/v2g0/m_{'a' * 31}/doc.txt")
    assert not s3_jobs_gc.material_key_single_use(f"ws1/{'b' * 64}/v2gx/{mid}/doc.txt")
    # 真 job 产物（第 3 段 job id 恒含下划线）。
    assert not s3_jobs_gc.material_key_single_use("jobs/ws-x/ws-x_q1/dead.json")


def test_rollback_exemption_excludes_material_shaped_keys() -> None:
    """#1295（#1285 同族残留）：豁免判定叠加材料形态排除——撞名前缀下
    第 3 段为 g{代次} 的材料形态 key 不豁免（其 .rollback 段是材料文件
    的合法相对路径段，非 promote 回滚备份）；同前缀下的真回滚备份（第
    3 段是 job id）仍豁免。"""
    assert not s3_jobs_gc._rollback_backup_exempt(
        f"jobs-staging/{'a' * 64}/g2/dir/.rollback/file.bin"
    )
    assert not s3_jobs_gc._rollback_backup_exempt(f"jobs/{'b' * 64}/g0/.rollback/file.bin")
    assert s3_jobs_gc._rollback_backup_exempt("jobs-staging/ws-1/job-1/e1/.rollback/att-1/out.json")
    assert s3_jobs_gc._rollback_backup_exempt("jobs/ws-1/job-1/.rollback/x.json")


def test_staging_scan_does_not_swallow_material_key_with_rollback_segment() -> None:
    """#1295：workspace id 字面为 jobs-staging 时，材料 key
    （{ws}/{hash}/g{代次}/dir/.rollback/file.bin）落在 staging 前缀扫描
    内。扫描期 /.rollback/ 豁免若先于材料形态分流命中，该 key 既不删除
    也不进报告（永久不可见）。豁免叠加材料形态排除后：无行且超窗的该
    形态 key 进 staging_orphans（由 CLI 分流进材料只报告集合）；真回滚
    备份仍被豁免。"""
    material_key = f"jobs-staging/{'a' * 64}/g2/dir/.rollback/file.bin"
    report = scan_orphans(
        _lister(
            {
                "jobs-staging/": [
                    _entry(material_key),
                    _entry("jobs-staging/ws-1/job-1/e1/.rollback/att-1/out.json"),
                ]
            }
        ),
        lambda keys: set(),
        now=NOW,
    )
    assert [o.key for o in report.staging_orphans] == [material_key]


def test_apply_gc_rollback_exemption_does_not_cover_material_shaped_keys() -> None:
    """#1295 + #1314：撞名前缀下含 .rollback 段的材料形态 key 不继承回滚
    豁免（豁免判定叠加材料形态排除，见上方豁免判定用例），也不在
    apply_gc 删除——材料形态 key 只经 s3_material_gc 的门禁删除（见
    tests/services/test_s3_material_gc.py）；同前缀的真回滚备份仍不可删，
    同前缀的普通 staging 孤儿照删。"""
    deleter = _RecordingDeleter()
    material_key = f"jobs-staging/{'a' * 64}/g2/dir/.rollback/file.bin"
    report = _report_with(
        authority=[],
        staging=[
            "jobs-staging/ws-1/job-1/e1/.rollback/att-1/out.json",
            "jobs-staging/ws-1/job-1/e1/plain.json",
            material_key,
        ],
    )

    result = s3_jobs_gc.apply_gc(deleter, "bkt", report)  # 无 revalidate 的最坏路径

    assert deleter.deleted == ["jobs-staging/ws-1/job-1/e1/plain.json"]
    assert result.deleted == 1


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

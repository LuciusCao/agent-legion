"""Unit coverage for the rerun artifact cleanup's re-attempt guards (#508,
#683 review P1, #706 review P2).

The cleanup runs AFTER the rerun transaction commits — from that instant the
job is schedulable again, so a re-attempt's ``promote_all`` (authority-key
object copy first, ``record_remote_many`` manifest rows last) can interleave
with the cleanup at any point. These tests script the manifest timeline via
an injectable store double: the Nth ``live_keys_for`` probe returns the Nth
state, so each interleaving lands deterministically between the cleanup's
batch probe, its per-object re-validation probes, and the post-deletion
diagnostic re-check. The probes are targeted existence queries — the double
records every probed key set so the P2 regression (no full-manifest reads,
key-set-sized transfers) is pinned alongside the race semantics.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.services.rerun_artifact_cleanup import delete_rerun_artifact_objects


class _ManifestTimelineStore:
    """JobArtifactObjectStore double with a scripted manifest timeline.

    ``states`` is a list indexed by ``live_keys_for`` call count (1-based,
    clamped to the last entry): each entry is the manifest the store exposes
    at that moment. The probe answers with the subset of that state matching
    the queried keys — so tests also pin the targeted-key contract. Deletions
    are recorded, never fail. Every probe's key set is appended to
    ``probed_keys`` for the query-shape assertions.
    """

    def __init__(self, states: list[list[dict[str, Any]]]) -> None:
        self.enabled = True
        self._states = states
        self._reads = 0
        self.deleted_keys: list[str] = []
        self.probed_keys: list[list[str]] = []

    def live_keys_for(self, job_id: str, storage_keys: list[str]) -> set[str]:
        self._reads += 1
        self.probed_keys.append(list(storage_keys))
        state = self._states[min(self._reads, len(self._states)) - 1]
        wanted = set(storage_keys)
        return {str(row["storage_key"]) for row in state if str(row["storage_key"]) in wanted}

    def delete_objects(self, rows: list[dict[str, Any]]) -> None:
        self.deleted_keys.extend(str(row["storage_key"]) for row in rows)


_UP_KEY = "jobs/ws-1/job-1/up.json"
_DOWN_KEY = "jobs/ws-1/job-1/down.json"
_SNAPSHOT = [
    {"node_key": "up", "name": "up.json", "storage_key": _UP_KEY},
    {"node_key": "down", "name": "down.json", "storage_key": _DOWN_KEY},
]
_FRESH_UP_ROW = {"node_key": "up", "name": "up.json", "storage_key": _UP_KEY}


def test_promote_lands_between_snapshot_and_deletes_spares_fresh_key():
    """#683 review P1 交错：批量探测之后、删除之前，新 attempt 完成了
    promote_all（对象拷到同一稳定权威键 + record_remote_many 提交清单行）。
    逐对象删除前的当前清单重验必须放过该键——旧代码按快照盲删会让新清单
    行指向不存在的对象。未被复现的孤儿键照删。"""
    # probe#1 = batch entry check: manifest empty (rerun txn dropped the
    # rows, the re-attempt has not registered yet). probe#2+ = per-object
    # re-validation / post-check: promote_all completed in the gap.
    store = _ManifestTimelineStore([[], [_FRESH_UP_ROW]])

    delete_rerun_artifact_objects(store, _SNAPSHOT, "job-1", "rerun")

    assert store.deleted_keys == [_DOWN_KEY], "fresh authority key must be spared"
    assert _UP_KEY not in store.deleted_keys
    # Per-object probes carry exactly the key they are about to remove —
    # never the whole manifest (#706 review P2).
    assert store.probed_keys[1] == [_UP_KEY]
    assert store.probed_keys[2] == [_DOWN_KEY]


def test_re_registered_before_cleanup_starts_is_batch_skipped():
    """promote_all 在清理开始前已完整完成：入口的批量重验（#508 原有语义）
    直接把已复现键挡在删除集之外。"""
    store = _ManifestTimelineStore([[_FRESH_UP_ROW]])

    delete_rerun_artifact_objects(store, _SNAPSHOT, "job-1", "rerun")

    assert store.deleted_keys == [_DOWN_KEY]


def test_residual_window_race_still_logs_diagnostic(caplog):
    """残余毫秒窗口（单对象重读→该对象删除调用之间 promote 才落地）只能靠
    条件删除/版本化 key 关闭——此窗口命中时对象被删、新行随后指向空对象，
    删除后的二次检查必须继续把搁浅行告警出来（诊断不回退）。"""
    # probe#1 = batch entry check: empty. probe#2 = per-object check for
    # "up": still empty (promote has not landed) → up deletes. Between that
    # and probe#3 the promote completes: probe#3 = check for "down" (fresh
    # row for up is live, down is not) → down deletes. probe#4 = post-check
    # sees the fresh row under just-deleted up key → warning.
    store = _ManifestTimelineStore([[], [], [_FRESH_UP_ROW]])

    with caplog.at_level(logging.WARNING, logger="server.app.services.rerun_artifact_cleanup"):
        delete_rerun_artifact_objects(store, _SNAPSHOT, "job-1", "rerun")

    assert set(store.deleted_keys) == {_UP_KEY, _DOWN_KEY}
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "re-attempt raced the cleanup" in warnings[0].message
    assert _UP_KEY in str(warnings[0].message)


def test_store_without_query_seam_deletes_all():
    """无查询缝隙的 store（getattr 缺省 = 空结果）保持原有退化语义：全删。"""

    class _SeamlessStore:
        enabled = True

        def __init__(self) -> None:
            self.deleted_keys: list[str] = []

        def delete_objects(self, rows: list[dict[str, Any]]) -> None:
            self.deleted_keys.extend(str(row["storage_key"]) for row in rows)

    store = _SeamlessStore()
    delete_rerun_artifact_objects(store, _SNAPSHOT, "job-1", "rerun")
    assert set(store.deleted_keys) == {_UP_KEY, _DOWN_KEY}


def test_disabled_or_empty_inputs_are_noop():
    class _ProbeStore:
        enabled = True

        def __init__(self) -> None:
            self.deleted_keys: list[str] = []
            self.probes = 0

        def live_keys_for(self, job_id: str, storage_keys: list[str]) -> set[str]:
            self.probes += 1
            return set()

        def delete_objects(self, rows: list[dict[str, Any]]) -> None:
            self.deleted_keys.extend(str(row["storage_key"]) for row in rows)

    store = _ProbeStore()
    delete_rerun_artifact_objects(None, _SNAPSHOT, "job-1", "rerun")
    store.enabled = False
    delete_rerun_artifact_objects(store, _SNAPSHOT, "job-1", "rerun")
    delete_rerun_artifact_objects(store, [], "job-1", "rerun")
    assert store.deleted_keys == []
    assert store.probes == 0, "disabled/empty inputs must not reach the store"


def test_probe_shaping_no_full_manifest_reads_for_n_objects():
    """#706 review P2：N 个待删对象不得再产生 N 次全量清单读取。全部
    重验走靶向存在性探针（live_keys_for），rows_for_job（整份清单传输）
    一次也不得被触碰；探针次数为 1（批量）+ N（逐对象）+ 1（删除后诊断，
    仅在实际删了东西时），且每条探针只携带待验键集。"""
    keys = [f"jobs/ws-1/job-1/f{i}.json" for i in range(5)]
    rows = [
        {"node_key": f"n{i}", "name": f"f{i}.json", "storage_key": key}
        for i, key in enumerate(keys)
    ]

    class _CountingStore:
        enabled = True

        def __init__(self) -> None:
            self.probes: list[list[str]] = []

        def rows_for_job(self, job_id: str) -> list[dict[str, Any]]:
            raise AssertionError("full-manifest read must not be used by the cleanup")

        def live_keys_for(self, job_id: str, storage_keys: list[str]) -> set[str]:
            self.probes.append(list(storage_keys))
            return set()  # nothing re-registered → every key stays stale

        def delete_objects(self, rows: list[dict[str, Any]]) -> None:
            pass

    store = _CountingStore()
    delete_rerun_artifact_objects(store, rows, "job-1", "rerun")

    assert len(store.probes) == 1 + len(rows) + 1
    assert set(store.probes[0]) == set(keys), "batch probe carries the retired key set"
    for single in store.probes[1:-1]:
        assert len(single) == 1, "per-object re-validation probes exactly the key it removes"
    assert set(store.probes[-1]) == set(keys), "post-check probes only the removed keys"


def test_post_check_probe_skipped_when_nothing_removed():
    """全部键在批量重验时已复现：没有删除就没有竞态可言，删除后诊断探针
    随之省去（探针次数 = 1，不再多发空查）。"""
    live_rows = [
        {"node_key": row["node_key"], "name": row["name"], "storage_key": row["storage_key"]}
        for row in _SNAPSHOT
    ]
    store = _ManifestTimelineStore([live_rows])

    delete_rerun_artifact_objects(store, _SNAPSHOT, "job-1", "rerun")

    assert store.deleted_keys == []
    assert len(store.probed_keys) == 1, "batch probe only — no per-object or post-check probes"


def test_probe_failure_is_logged_not_raised(caplog):
    """#759 P1：post-commit 清理不得反转已提交的 mutation——批量探针
    （live_keys_for）抛错只记日志、不上抛（否则路由把成功报成 500、批量
    调用方中断整批）。突变自检锚点：无兜底的实现会让 RuntimeError 直接
    冒出，本用例变红。"""
    probes = {"count": 0}

    class _FailingProbeStore:
        enabled = True

        def live_keys_for(self, job_id: str, storage_keys: list[str]) -> set[str]:
            probes["count"] += 1
            raise RuntimeError("manifest probe boom")

        def delete_objects(self, rows: list[dict[str, Any]]) -> None:
            raise AssertionError("unreachable: the batch probe already failed")

    with caplog.at_level(logging.ERROR, logger="server.app.services.rerun_artifact_cleanup"):
        delete_rerun_artifact_objects(_FailingProbeStore(), _SNAPSHOT, "job-1", "rerun")

    assert probes["count"] == 1
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "job-1" in errors[0].message
    assert "rerun" in errors[0].message


def test_delete_failure_is_logged_not_raised(caplog):
    """#759 P1：逐对象删除（delete_objects）抛错同样被吞并记日志（带
    job_id 与调用方域名），不向 upgrade/rerun/run-to 的调用方传播。"""
    calls = {"count": 0}

    class _FailingDeleteStore:
        enabled = True

        def live_keys_for(self, job_id: str, storage_keys: list[str]) -> set[str]:
            return set()

        def delete_objects(self, rows: list[dict[str, Any]]) -> None:
            calls["count"] += 1
            raise RuntimeError("delete boom")

    with caplog.at_level(logging.ERROR, logger="server.app.services.rerun_artifact_cleanup"):
        delete_rerun_artifact_objects(_FailingDeleteStore(), _SNAPSHOT, "job-1", "upgrade-workflow")

    assert calls["count"] == 1, "the first failing delete aborts the walk; contained"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "job-1" in errors[0].message
    assert "upgrade-workflow" in errors[0].message

"""Unit coverage for the rerun artifact cleanup's re-attempt guards (#508,
#683 review P1).

The cleanup runs AFTER the rerun transaction commits — from that instant the
job is schedulable again, so a re-attempt's ``promote_all`` (authority-key
object copy first, ``record_remote_many`` manifest rows last) can interleave
with the cleanup at any point. These tests script the manifest timeline via
an injectable store double: the Nth ``rows_for_job`` call returns the Nth
state, so each interleaving lands deterministically between the cleanup's
batch snapshot read, its per-object re-validation reads, and the
post-deletion diagnostic re-check.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.services.rerun_artifact_cleanup import delete_rerun_artifact_objects


class _ManifestTimelineStore:
    """JobArtifactObjectStore double with a scripted manifest timeline.

    ``states`` is a list indexed by ``rows_for_job`` call count (1-based,
    clamped to the last entry): each entry is the list of manifest rows the
    store exposes at that moment. Deletions are recorded, never fail.
    """

    def __init__(self, states: list[list[dict[str, Any]]]) -> None:
        self.enabled = True
        self._states = states
        self._reads = 0
        self.deleted_keys: list[str] = []

    def rows_for_job(self, job_id: str) -> list[dict[str, Any]]:
        self._reads += 1
        state = self._states[min(self._reads, len(self._states)) - 1]
        return [dict(row) for row in state]

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
    """#683 review P1 交错：批量快照读之后、删除之前，新 attempt 完成了
    promote_all（对象拷到同一稳定权威键 + record_remote_many 提交清单行）。
    逐对象删除前的当前清单重验必须放过该键——旧代码按快照盲删会让新清单
    行指向不存在的对象。未被复现的孤儿键照删。"""
    # read#1 = batch snapshot: manifest empty (rerun txn dropped the rows,
    # the re-attempt has not registered yet). read#2+ = per-object
    # re-validation / post-check: promote_all completed in the gap.
    store = _ManifestTimelineStore([[], [_FRESH_UP_ROW]])

    delete_rerun_artifact_objects(store, _SNAPSHOT, "job-1", "rerun")

    assert store.deleted_keys == [_DOWN_KEY], "fresh authority key must be spared"
    assert _UP_KEY not in store.deleted_keys


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
    # read#1 = batch snapshot: empty. read#2 = per-object check for "up":
    # still empty (promote has not landed) → up deletes. Between that and
    # read#3 the promote completes: read#3 = check for "down" (fresh row for
    # up is live, down is not) → down deletes. read#4 = post-check sees the
    # fresh row under just-deleted up key → warning.
    store = _ManifestTimelineStore([[], [], [_FRESH_UP_ROW]])

    with caplog.at_level(logging.WARNING, logger="server.app.services.rerun_artifact_cleanup"):
        delete_rerun_artifact_objects(store, _SNAPSHOT, "job-1", "rerun")

    assert set(store.deleted_keys) == {_UP_KEY, _DOWN_KEY}
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "re-attempt raced the cleanup" in warnings[0].message
    assert _UP_KEY in str(warnings[0].message)


def test_store_without_query_seam_deletes_all():
    """无查询缝隙的 store（getattr 缺省 = 空清单）保持原有退化语义：全删。"""

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

        def rows_for_job(self, job_id: str) -> list[dict[str, Any]]:
            return []

        def delete_objects(self, rows: list[dict[str, Any]]) -> None:
            self.deleted_keys.extend(str(row["storage_key"]) for row in rows)

    store = _ProbeStore()
    delete_rerun_artifact_objects(None, _SNAPSHOT, "job-1", "rerun")
    store.enabled = False
    delete_rerun_artifact_objects(store, _SNAPSHOT, "job-1", "rerun")
    delete_rerun_artifact_objects(store, [], "job-1", "rerun")
    assert store.deleted_keys == []

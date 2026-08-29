"""sync_host_status 的 remote 状态组装测试：注册明细随每次同步发布。

回归背景：set_remote 是整体替换语义，注册返回的 workspace 明细若只在
executor 启动时合并一次，第一次周期性心跳（默认 15s）就会用不含该字段的
remote 字典把它抹掉，控制台 token 卡片随之退化为兜底文案。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import worker.registration.retry as registration_retry
from worker.host.status_sync import sync_host_status
from worker.metrics_cache import WorkerMetricsCache
from worker.status import ExecutionStatusReporter, read_runtime_status

pytestmark = pytest.mark.no_db


class _FakeClient:
    """只实现 get_self 的桩；metrics 走 FakeMetricsClient 同款空窗口。"""

    token = "must-never-reach-the-cache"

    def get_ops_metrics(self, granularity: str) -> dict[str, Any]:
        return {"granularity": granularity, "buckets": []}

    def get_self(self) -> dict[str, Any]:
        return {"worker_id": "worker-1", "revoked": False, "allowed_workspaces": ["ws-1"]}


def _publish_once(
    tmp_path: Path,
    workspaces: list[dict[str, Any]],
    client: _FakeClient | None = None,
    previous: dict[str, Any] | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> dict[str, Any]:
    patch = monkeypatch or pytest.MonkeyPatch()
    patch.setattr(registration_retry, "_last_registration_workspaces", workspaces)
    try:
        sync_host_status(
            client or _FakeClient(),  # type: ignore[arg-type]
            ExecutionStatusReporter(tmp_path / "status.json"),
            WorkerMetricsCache(tmp_path / "metrics.json", refresh_seconds=60),
            previous,
        )
    finally:
        if monkeypatch is None:
            patch.undo()
    return read_runtime_status(tmp_path / "status.json")["remote"]


def test_sync_publishes_registration_workspaces(tmp_path: Path) -> None:
    workspaces = [{"workspace_id": "ws-1", "workspace_name": "内容生产", "token_ids": ["tok-1"]}]

    remote = _publish_once(tmp_path, workspaces)

    assert remote["workspaces"] == workspaces


def test_sync_filters_workspaces_to_current_host_scope(tmp_path: Path) -> None:
    """Host 删除某个 scoped token 后会收窄存活 Worker 的 allowed_workspaces；
    明细必须按当前 scope 过滤，否则控制台会一直显示已删除的 workspace。"""
    workspaces = [
        {"workspace_id": "ws-1", "workspace_name": "内容生产", "token_ids": ["tok-1"]},
        {"workspace_id": "ws-2", "workspace_name": "已删除", "token_ids": ["tok-2"]},
    ]

    class NarrowedScopeClient(_FakeClient):
        def get_self(self) -> dict[str, Any]:
            return {"worker_id": "worker-1", "revoked": False, "allowed_workspaces": ["ws-1"]}

    remote = _publish_once(tmp_path, workspaces, NarrowedScopeClient())

    assert [row["workspace_id"] for row in remote["workspaces"]] == ["ws-1"]


def test_sync_host_unreachable_filters_by_last_known_scope(tmp_path: Path) -> None:
    """executor 的不可达路径把上一次成功的 get_self 结果作为 previous 传入；
    明细按该最后已知 scope 过滤，而非无差别保留。"""
    workspaces = [
        {"workspace_id": "ws-1", "workspace_name": "内容生产", "token_ids": ["tok-1"]},
        {"workspace_id": "ws-2", "workspace_name": "已删除", "token_ids": ["tok-2"]},
    ]

    class UnreachableClient(_FakeClient):
        def get_self(self) -> dict[str, Any]:
            raise RuntimeError("connection refused")

    remote = _publish_once(
        tmp_path,
        workspaces,
        UnreachableClient(),
        previous={"worker_id": "worker-1", "revoked": False, "allowed_workspaces": ["ws-1"]},
    )

    assert remote["host_reachable"] is False
    assert [row["workspace_id"] for row in remote["workspaces"]] == ["ws-1"]


def test_sync_without_workspaces_omits_the_field(tmp_path: Path) -> None:
    """尚未注册成功（明细为空）时字段缺省，不写空列表占位。"""

    remote = _publish_once(tmp_path, [])

    assert "workspaces" not in remote


def test_sync_without_worker_view_keeps_unfiltered_workspaces(tmp_path: Path) -> None:
    """没有可用 worker 视图（鉴权拒绝后重同步失败、或首次同步即失败）时
    无 scope 可言，明细原样保留，恢复后的下一次同步重新对齐。"""
    workspaces = [{"workspace_id": "ws-1", "workspace_name": "内容生产", "token_ids": ["tok-1"]}]

    class UnreachableClient(_FakeClient):
        def get_self(self) -> dict[str, Any]:
            raise RuntimeError("connection refused")

    remote = _publish_once(tmp_path, workspaces, UnreachableClient(), previous=None)

    assert remote["host_reachable"] is False
    assert remote["workspaces"] == workspaces

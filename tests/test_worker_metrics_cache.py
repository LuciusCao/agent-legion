from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from worker.host_client import WorkerAuthError
from worker.host_status_sync import sync_host_status
from worker.metrics_cache import (
    METRIC_WINDOWS,
    WorkerMetricsCache,
    metrics_cache_key,
    read_metrics_cache,
)
from worker.status import ExecutionStatusReporter, read_runtime_status


class FakeMetricsClient:
    token = "must-never-reach-the-cache"

    def __init__(self, failing: str | None = None) -> None:
        self.calls: list[str] = []
        self.failing = failing

    def get_ops_metrics(self, granularity: str) -> dict[str, Any]:
        self.calls.append(granularity)
        if granularity == self.failing:
            raise RuntimeError("Host unavailable")
        return {"granularity": granularity, "buckets": []}


def test_refresh_publishes_fixed_windows_without_worker_token(tmp_path: Path) -> None:
    path = tmp_path / "ops_metrics.json"
    client = FakeMetricsClient()
    cache = WorkerMetricsCache(path, refresh_seconds=60)

    cache.refresh(client, now=100)
    cache.refresh(client, now=101)

    assert client.calls == list(METRIC_WINDOWS)
    payload = read_metrics_cache(path)
    assert set(payload["snapshots"]) == {"6h", "24h", "30d"}
    assert payload["error"] is None
    assert client.token not in path.read_text(encoding="utf-8")


def test_refresh_preserves_successful_windows_and_reports_partial_error(tmp_path: Path) -> None:
    path = tmp_path / "ops_metrics.json"
    cache = WorkerMetricsCache(path, refresh_seconds=0)
    cache.refresh(FakeMetricsClient(), now=100)
    cache.refresh(FakeMetricsClient(failing="24h"), now=101)

    payload = read_metrics_cache(path)

    assert payload["snapshots"][metrics_cache_key("24h")]["granularity"] == "24h"
    assert payload["error"] == "24h: Host unavailable"


def test_read_metrics_cache_ignores_dead_writer_and_malformed_file(tmp_path: Path) -> None:
    path = tmp_path / "ops_metrics.json"
    path.write_text(
        json.dumps({"pid": 99999999, "snapshots": {"6h": {"secret": True}}}),
        encoding="utf-8",
    )
    assert read_metrics_cache(path)["snapshots"] == {}

    path.write_text("{broken", encoding="utf-8")
    assert read_metrics_cache(path)["snapshots"] == {}


def test_sync_host_status_refreshes_metrics_after_worker_authentication(tmp_path: Path) -> None:
    client = FakeMetricsClient()
    client.get_self = lambda: {"worker_id": "worker-1", "revoked": False}  # type: ignore[attr-defined]
    status_path = tmp_path / "status.json"
    metrics_path = tmp_path / "ops_metrics.json"

    worker = sync_host_status(
        client,  # type: ignore[arg-type]
        ExecutionStatusReporter(status_path),
        WorkerMetricsCache(metrics_path, refresh_seconds=0),
        None,
    )

    assert worker == {"worker_id": "worker-1", "revoked": False}
    assert read_runtime_status(status_path)["remote"]["registered"] is True
    assert set(read_metrics_cache(metrics_path)["snapshots"]) == {
        "6h",
        "24h",
        "30d",
    }


def test_sync_host_status_propagates_worker_authentication_rejection(tmp_path: Path) -> None:
    class RejectedClient(FakeMetricsClient):
        def get_self(self) -> dict[str, Any]:
            raise WorkerAuthError("invalid token")

    status_path = tmp_path / "status.json"
    with pytest.raises(WorkerAuthError):
        sync_host_status(
            RejectedClient(),  # type: ignore[arg-type]
            ExecutionStatusReporter(status_path),
            WorkerMetricsCache(tmp_path / "metrics.json"),
            {"worker_id": "worker-1"},
        )

    remote = read_runtime_status(status_path)["remote"]
    assert remote["host_reachable"] is True
    assert remote["registered"] is False
    assert remote["connection_error"] == "invalid token"


def test_sync_host_status_keeps_registration_workspaces_across_heartbeats(tmp_path: Path) -> None:
    """回归：set_remote 是整体替换，周期性心跳若不带 workspaces 会把启动时
    写入的 workspace 明细抹掉，控制台 token 卡片随之退化为兜底文案。"""
    client = FakeMetricsClient()
    client.get_self = lambda: {"worker_id": "worker-1", "revoked": False}  # type: ignore[attr-defined]
    status_path = tmp_path / "status.json"
    reporter = ExecutionStatusReporter(status_path)
    metrics = WorkerMetricsCache(tmp_path / "metrics.json", refresh_seconds=60)
    workspaces = [
        {
            "workspace_id": "ws-1",
            "workspace_name": "内容生产",
            "token_ids": ["tok-1"],
        }
    ]

    import worker.registration_retry as registration_retry

    registration_retry._last_registration_workspaces = workspaces
    try:
        sync_host_status(client, reporter, metrics, None)  # type: ignore[arg-type]
        # 第二次同步模拟 15s 心跳后的再次调用——字段必须原样保留。
        sync_host_status(client, reporter, metrics, None)  # type: ignore[arg-type]

        remote = read_runtime_status(status_path)["remote"]
        assert remote["workspaces"] == workspaces
    finally:
        registration_retry._last_registration_workspaces = []


def test_sync_host_status_keeps_workspaces_when_host_unreachable(tmp_path: Path) -> None:
    class UnreachableClient(FakeMetricsClient):
        def get_self(self) -> dict[str, Any]:
            raise RuntimeError("connection refused")

    status_path = tmp_path / "status.json"
    workspaces = [{"workspace_id": "ws-1", "workspace_name": "内容生产", "token_ids": ["tok-1"]}]

    import worker.registration_retry as registration_retry

    registration_retry._last_registration_workspaces = workspaces
    try:
        sync_host_status(
            UnreachableClient(),  # type: ignore[arg-type]
            ExecutionStatusReporter(status_path),
            WorkerMetricsCache(tmp_path / "metrics.json"),
            {"worker_id": "worker-1"},
        )

        remote = read_runtime_status(status_path)["remote"]
        assert remote["host_reachable"] is False
        assert remote["workspaces"] == workspaces
    finally:
        registration_retry._last_registration_workspaces = []


def test_sync_host_status_without_workspaces_omits_the_field(tmp_path: Path) -> None:
    """尚未注册成功（明细为空）时行为不变：字段缺省而非空列表占位。"""
    client = FakeMetricsClient()
    client.get_self = lambda: {"worker_id": "worker-1", "revoked": False}  # type: ignore[attr-defined]
    status_path = tmp_path / "status.json"

    sync_host_status(
        client,  # type: ignore[arg-type]
        ExecutionStatusReporter(status_path),
        WorkerMetricsCache(tmp_path / "metrics.json"),
        None,
    )

    assert "workspaces" not in read_runtime_status(status_path)["remote"]

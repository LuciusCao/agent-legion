"""GET /api/workspaces/{workspace_id}/runtime-models 路由与聚合测试。

Studio 节点执行 datalist 的数据源：workspace 在线 Worker 声明的
(runtime, provider, model) 三元组聚合（schema v64 起 workspace Agent
默认配置退役，节点 execution 的自由输入靠它提示可 claim 的型号）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.services.workspace_runtime_models import workspace_runtime_models
from tests.helpers.agent_worker_api import (
    authenticate_admin,
    issue_scoped_token,
    make_app,
    register,
)


def test_runtime_models_aggregates_online_workspace_workers(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as client:
        authenticate_admin(client)
        register(
            client,
            worker_id="pi-worker",
            runtimes=["pi"],
            models=[
                {"runtime": "pi", "provider": "deepseek", "model": "v4-flash"},
                {"runtime": "pi", "provider": "deepseek", "model": "v4-pro"},
            ],
            protocol_version=3,
        )
        register(
            client,
            worker_id="velites-worker",
            # 声明两个 runtime：models 里的 pi 条目必须属于已声明 runtime。
            runtimes=["pi", "velites"],
            models=[
                {"runtime": "velites", "provider": "sqai", "model": "k2"},
                # 跨 worker 去重：与 pi-worker 重叠的声明只出现一次。
                {"runtime": "pi", "provider": "deepseek", "model": "v4-pro"},
            ],
            protocol_version=3,
        )
        # 别的 workspace 的 worker 不参与本 workspace 的聚合。
        other = issue_scoped_token(client, workspace_id="other-workspace")
        register(
            client,
            credential=other,
            worker_id="other-worker",
            runtimes=["pi"],
            models=[{"runtime": "pi", "provider": "other", "model": "m"}],
            protocol_version=3,
        )

        response = client.get("/api/workspaces/test-workspace/runtime-models")

    assert response.status_code == 200
    assert response.json()["runtimes"] == {
        "pi": {"deepseek": ["v4-flash", "v4-pro"]},
        "velites": {"sqai": ["k2"]},
    }


def test_runtime_models_empty_without_workers(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as client:
        authenticate_admin(client)
        issue_scoped_token(client)  # creates test-workspace

        response = client.get("/api/workspaces/test-workspace/runtime-models")

    assert response.status_code == 200
    assert response.json()["runtimes"] == {}


def test_runtime_models_requires_auth(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as client:
        response = client.get("/api/workspaces/test-workspace/runtime-models")

    assert response.status_code == 401


class _FakeRegistry:
    def __init__(self, workers: list[dict]) -> None:
        self._workers = workers

    def list_workers(self, workspace_id: str) -> list[dict]:
        return self._workers


@pytest.mark.no_db
def test_aggregation_skips_offline_workers_and_keeps_wildcards() -> None:
    registry = _FakeRegistry(
        [
            {
                "online": True,
                "models": [
                    {"runtime": "pi", "provider": "deepseek", "model": "v4"},
                    {"runtime": "*", "provider": "*", "model": "*"},
                ],
            },
            {
                "online": False,
                "models": [{"runtime": "pi", "provider": "offline", "model": "m"}],
            },
        ]
    )

    assert workspace_runtime_models(registry, "ws") == {
        "*": {"*": ["*"]},
        "pi": {"deepseek": ["v4"]},
    }


@pytest.mark.no_db
def test_aggregation_drops_entries_without_provider_or_model() -> None:
    registry = _FakeRegistry(
        [
            {
                "online": True,
                "models": [
                    {"runtime": "pi", "provider": "", "model": "v4"},
                    {"runtime": "pi", "provider": "deepseek", "model": ""},
                ],
            }
        ]
    )

    assert workspace_runtime_models(registry, "ws") == {}

"""Remote Worker 注册面 interop 测试（issue #297 第二项）。

两个既有测试面各自 mock 掉了对端：

* tests/routes/test_agent_worker_registration.py 用 TestClient 直接打
  Host 路由——worker/host/client.py 的拼装/解析逻辑没有参与；
* tests/workers/test_agent_worker.py 给 Client.request 打桩——Host 路由、
  registry、register_tokens.py 的语义没有参与。

remote 部署面（docs/remote-execution-runbook.md §5、
docs/agent-worker-deployment.md §4「token 即 scope」）的关键行为恰好横跨
两侧：**真实的 Worker Client（requests 会话）对真实的 Host 路由**。这里用
一个 requests → httpx-ASGI 的桥把两边接起来，每条用例都是完整链路：

* 多 scoped token 并集注册（union scope + per-workspace 明细回显）；
* 任一 token 失效 → 整单 401（Client 侧是 WorkerAuthError，scope 不被静默
  缩小）；
* key 删除的级联语义（register_token_deletion.py）：仅绑该 key 的 Worker
  记录同事务消失、worker_token 立即失效；多 key Worker 收窄到存活 scope；
* 注册后 get_self / claim 用 worker_token 的鉴权闭环。

纯 register/claim 控制面——bundle/artifact 传输在
tests/workers/test_worker_host_client.py 有独立覆盖，这里不重复。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import requests
from fastapi.testclient import TestClient

from server.app.main import create_app
from tests.helpers.agent_worker_api import authenticate_admin
from tests.helpers.agent_worker_api import issue_scoped_token as _issue_scoped_token
from worker.host.client import Client, WorkerAuthError

# Worker 控制面注册请求的固定形状：与 worker/host/client.py::register 拼
# 出的字段一致（protocol_version 由 Client 按本机 PROTOCOL_VERSION 填写）。
_WORKER_CONFIG: dict[str, Any] = {
    "worker_id": "remote-mini",
    "name": "Remote Mac mini",
    "runtimes": ["pi"],
    "models": [],
    "max_concurrency": 4,
    "max_code_concurrency": 0,
    "labels": {"os": "linux", "arch": "arm64"},
}

# 桥接占位 host：不解析、不出网，只标识「这条 requests 调用走 ASGI 桥」。
_BRIDGE_HOST = "host.test"


def _to_requests_response(test_response) -> requests.Response:  # noqa: ANN001
    response = requests.Response()
    response.status_code = test_response.status_code
    response.headers.update(test_response.headers)
    response._content = test_response.content
    response.url = str(test_response.request.url)
    return response


@pytest.fixture
def admin_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """一个真实 Host app + 它的 admin TestClient + 接到同一 app 的
    requests→TestClient 桥（Worker Client 的出站流量走它）。

    本 fixture 刻意不用 conftest 的共享 client（codex review on #310 提出，
    评估后不采纳，理由如下）：共享 app 的 teardown invariant 要求
    ``agent_manager.agents`` 在每个测试后为空——而 worker 注册恰恰会向它
    写入。全部既有注册面测试（tests/routes/test_agent_worker_registration.py）
    因此都用私有 app；本测试属于同一族。admin bootstrap 走与 conftest 相同
    的 ``authenticate_admin`` 帮助函数（同一认证/CSRF 路径），app 级隔离用
    per-test data_dir。桥用一个**不进入上下文**的独立 TestClient 实例转发
    请求（admin 的上下文只进一次——重复进入会重跑 lifespan 并炸 shutdown
    hooks），cookie jar 与 admin 会话隔离。
    """
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        authenticate_admin(client)
        bridge = TestClient(app, base_url=f"http://{_BRIDGE_HOST}")
        original_request = requests.sessions.Session.request

        def bridged_request(session, method, url, *args, **kwargs):  # noqa: ANN002, ANN003
            if (urlparse(str(url)).hostname or "") != _BRIDGE_HOST:
                return original_request(session, method, url, *args, **kwargs)
            data = kwargs.get("data")
            return _to_requests_response(
                bridge.request(
                    method,
                    str(url),
                    headers=kwargs.get("headers") or {},
                    content=data if isinstance(data, bytes) else None,
                )
            )

        monkeypatch.setattr(requests.sessions.Session, "request", bridged_request)
        yield client
        bridge.close()


def _issue(admin: TestClient, workspace_id: str = "test-workspace") -> str:
    return _issue_scoped_token(admin, workspace_id=workspace_id)


def test_registration_presents_all_tokens_and_returns_union_scope(admin_client) -> None:
    """多 scoped token 并集注册：一个 Worker 凭两个 workspace 的 key 注册，
    Host 解析出并集 scope，并把每个 workspace 的明细（id/name/token_ids）
    回显给 Worker 控制台。"""
    first = _issue(admin_client, "test-workspace")
    second = _issue(admin_client, "other-workspace")

    client = Client(f"http://{_BRIDGE_HOST}")
    document = client.register(dict(_WORKER_CONFIG), [first, second])

    assert sorted(document["allowed_workspaces"]) == ["other-workspace", "test-workspace"]
    by_workspace = {row["workspace_id"]: row for row in document["workspaces"]}
    assert set(by_workspace) == {"test-workspace", "other-workspace"}
    assert by_workspace["test-workspace"]["token_ids"] == [first.partition(".")[0]]
    assert by_workspace["other-workspace"]["token_ids"] == [second.partition(".")[0]]
    assert all(row["workspace_name"] for row in document["workspaces"])
    # v3 注册握手：Client 校验 host_protocol_version >= 本机 PROTOCOL_VERSION
    # 才会走到这里（低版本 Host 会 fail-closed 拒绝注册）。
    assert document["host_protocol_version"] >= 3


def test_registration_with_one_dead_token_fails_whole_call(admin_client) -> None:
    """任一 token 失效（key 已删除）→ 整单 401：Worker Client 收到
    WorkerAuthError，scope 不会被静默缩小，Host 不留注册记录。"""
    live = _issue(admin_client, "test-workspace")
    dead = _issue(admin_client, "other-workspace")
    admin_client.delete(f"/api/agent-register-tokens/{dead.partition('.')[0]}")

    client = Client(f"http://{_BRIDGE_HOST}")
    with pytest.raises(WorkerAuthError, match="registration rejected: HTTP 401"):
        client.register(dict(_WORKER_CONFIG), [live, dead])

    workers = admin_client.get("/api/agent-workers").json()["workers"]
    assert all(w["worker_id"] != _WORKER_CONFIG["worker_id"] for w in workers)


def test_registration_with_unknown_token_is_rejected(admin_client) -> None:
    """未知 token（格式合法但 Host 无记录）与缺 secret 分隔符的 token 同样
    整单 401。"""
    live = _issue(admin_client)
    client = Client(f"http://{_BRIDGE_HOST}")
    for bogus in ("no-such-token-id.wrong-secret", "garbage-without-separator"):
        with pytest.raises(WorkerAuthError, match="HTTP 401"):
            client.register(dict(_WORKER_CONFIG), [live, bogus])


def test_delete_only_key_cascades_worker_record_and_cuts_token(admin_client) -> None:
    """key 删除级联：仅绑该 key 的 Worker 记录在同一事务中被删除，其
    worker_token 立即失效——下一次 claim / get_self 直接 401，不必等
    Worker 重启或重注册。"""
    credential = _issue(admin_client)
    token_id = credential.partition(".")[0]

    client = Client(f"http://{_BRIDGE_HOST}")
    client.register(dict(_WORKER_CONFIG), [credential])
    issued_worker_token = client.token

    # 注册发放的 worker_token 是有效凭证（get_self 鉴权闭环）。
    assert client.get_self()["worker_id"] == _WORKER_CONFIG["worker_id"]

    deleted = admin_client.delete(f"/api/agent-register-tokens/{token_id}")
    assert deleted.status_code == 200
    assert deleted.json()["cascaded_worker_ids"] == ["remote-mini"]

    # 级联是即时的：记录没了、凭证死了。
    with pytest.raises(WorkerAuthError):
        client.get_self()
    with pytest.raises(WorkerAuthError):
        client.claim(_WORKER_CONFIG["worker_id"])

    workers = admin_client.get("/api/agent-workers").json()["workers"]
    assert all(w["worker_id"] != _WORKER_CONFIG["worker_id"] for w in workers)
    assert issued_worker_token  # 前面的注册确实发放过凭证


def test_delete_one_key_narrows_multi_key_worker_to_surviving_scope(admin_client) -> None:
    """多 key Worker：删掉其中一个 key，记录保留但 scope 收窄到存活 key
    的 workspace（凭证仍可用）；再删最后一个 key 才级联删除记录。"""
    first = _issue(admin_client, "test-workspace")
    second = _issue(admin_client, "other-workspace")
    first_id = first.partition(".")[0]
    second_id = second.partition(".")[0]

    client = Client(f"http://{_BRIDGE_HOST}")
    client.register(dict(_WORKER_CONFIG), [first, second])

    deleted_first = admin_client.delete(f"/api/agent-register-tokens/{first_id}")
    assert deleted_first.json()["cascaded_worker_ids"] == []

    # 记录仍在，凭证仍活（get_self 走 worker_token 鉴权），scope 已收窄。
    self_view = client.get_self()
    assert self_view["allowed_workspaces"] == ["other-workspace"]
    assert self_view["register_token_ids"] == [second_id]

    deleted_last = admin_client.delete(f"/api/agent-register-tokens/{second_id}")
    assert deleted_last.json()["cascaded_worker_ids"] == ["remote-mini"]
    with pytest.raises(WorkerAuthError):
        client.get_self()


def test_re_registration_rotates_token_and_repoints_binding(admin_client) -> None:
    """重新注册轮换 worker_token 并把 key 绑定切到本次呈现的 token 集合。"""
    first = _issue(admin_client, "test-workspace")
    second = _issue(admin_client, "other-workspace")

    client = Client(f"http://{_BRIDGE_HOST}")
    client.register(dict(_WORKER_CONFIG), [first])
    first_worker_token = client.token

    client.register(dict(_WORKER_CONFIG), [first, second])
    assert client.token != first_worker_token

    self_view = client.get_self()
    assert sorted(self_view["register_token_ids"]) == sorted(
        [first.partition(".")[0], second.partition(".")[0]]
    )
    assert sorted(self_view["allowed_workspaces"]) == [
        "other-workspace",
        "test-workspace",
    ]


def test_registration_without_tokens_is_terminal_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有任何 register token 时 Client 端直接拒绝（零 HTTP 调用，
    monkeypatch 计数证明——subagent review on #310）：远程 Worker
    丢失全部 key 的 fail-fast 路径，不进入重试循环。"""
    calls: list[str] = []
    monkeypatch.setattr(
        requests.sessions.Session,
        "request",
        lambda session, method, url, *a, **kw: calls.append(str(url)),  # noqa: ANN002, ANN003
    )
    client = Client(f"http://{_BRIDGE_HOST}")
    with pytest.raises(WorkerAuthError, match="no register token"):
        client.register(dict(_WORKER_CONFIG), [])
    assert client.token == ""
    assert calls == []  # fail-fast happens before any request leaves


def test_claim_after_registration_returns_204_when_queue_empty(admin_client) -> None:
    """注册成功的 Worker 用 worker_token claim：空队列应答 204（None）而
    不是 401——scoped token 注册发放的凭证在 claim 闭环可用（对照 runbook
    §7「Claim returns 204 forever」排障行的健康基线）。"""
    credential = _issue(admin_client)
    client = Client(f"http://{_BRIDGE_HOST}")
    client.register(dict(_WORKER_CONFIG), [credential])

    assert client.claim(_WORKER_CONFIG["worker_id"], max_concurrency=4) is None

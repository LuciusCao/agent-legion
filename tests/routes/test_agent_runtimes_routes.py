"""`GET /api/agent-runtimes` 路由测试（#476）。

参照 tests/routes/test_infra_connections_routes.py 的挂载纪律：router 经
configure 回调挂到私有 app 上（中心 wiring 已挂时去重），分支本身独立可验。
"""

from __future__ import annotations

from server.app.routes.agent_runtimes import create_agent_runtimes_router

RUNTIMES_URL = "/api/agent-runtimes"


def _mount_agent_runtimes(app) -> None:
    """Mount the router unless the central wiring already did (dedupe)."""
    if any(getattr(route, "path", "") == RUNTIMES_URL for route in app.routes):
        return
    app.include_router(create_agent_runtimes_router(), prefix="/api")


def test_requires_auth(client_factory) -> None:
    with client_factory(authenticated=False, fresh=True, configure=_mount_agent_runtimes) as anon:
        assert anon.get(RUNTIMES_URL).status_code == 401


def test_lists_per_runtime_tool_catalog(client_factory) -> None:
    with client_factory(fresh=True, configure=_mount_agent_runtimes) as client:
        response = client.get(RUNTIMES_URL)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["runtimes"]) == {"pi", "velites"}

    velites = {tool["name"]: tool for tool in body["runtimes"]["velites"]["tools"]}
    # 三档齐全：default 预选、opt-in 显式开启、forced 锁定行带 activation。
    assert velites["read"]["tier"] == "default"
    assert velites["uuid"]["tier"] == "opt-in"
    assert velites["validate"]["tier"] == "forced"
    assert velites["validate"]["activation"] == "--require-output"
    assert "activation" not in velites["read"]
    # description/parameters 随 runtime 走（对 Studio 选项面有意义）。
    assert velites["uuid"]["description"]
    assert velites["uuid"]["parameters"]["required"] == ["op"]

    pi_names = [tool["name"] for tool in body["runtimes"]["pi"]["tools"]]
    assert pi_names == ["read", "write", "bash"]

"""Token-less api_base self-check (#915): does the MCP callback reach THIS instance?

A wrong registry ``api_base`` used to fail silently: kimi-code drops an MCP
server it cannot reach — or that answers 401 because it is another
agent-legion instance — and the chat session runs with zero platform tools.
The check must tell "this process" apart from "nothing there" and from
"someone else", without ever sending the scoped token.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount

from server.app.mcp_server.http_app import MCP_MOUNT_PATH, _ScopedTokenAuthApp
from server.app.mcp_server.instance_probe import PROBE_SUBPATH
from server.app.studio_chat import callback_check, serving_address
from server.app.studio_chat.callback_check import check_api_base, unreachable_detail

pytestmark = pytest.mark.no_db


@pytest.fixture(autouse=True)
def _fresh_state():
    callback_check.clear_callback_check_cache()
    serving_address.reset_serving_address_for_tests()
    yield
    callback_check.clear_callback_check_cache()
    serving_address.reset_serving_address_for_tests()


async def _never_reached(scope, receive, send) -> None:
    raise AssertionError("the probe must be answered by the guard, not the MCP app")


@pytest.fixture
def served_port() -> Iterator[int]:
    """The real MCP mount guard + serving-address middleware under uvicorn."""
    app = serving_address.ServingAddressMiddleware(
        Starlette(
            routes=[Mount(MCP_MOUNT_PATH, _ScopedTokenAuthApp(_never_reached, None))]  # type: ignore[arg-type]
        )
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield port
    server.should_exit = True
    thread.join(timeout=10)


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_self_check_passes_for_the_derived_default(served_port: int) -> None:
    # An unconfigured registry derives api_base from the serving address once
    # any request was served; that derived address must pass the self-check.
    httpx.get(f"http://127.0.0.1:{served_port}/api/health", trust_env=False)
    derived = serving_address.derived_api_base()
    assert derived == f"http://127.0.0.1:{served_port}"
    assert check_api_base(derived) is None


def test_probe_needs_no_token_and_rejects_bad_nonce(served_port: int) -> None:
    url = f"http://127.0.0.1:{served_port}{MCP_MOUNT_PATH}{PROBE_SUBPATH}"
    assert httpx.get(url, params={"nonce": "zz"}, trust_env=False).status_code == 400
    response = httpx.get(url, params={"nonce": "ab" * 16}, trust_env=False)
    assert response.status_code == 200
    assert set(response.json()) == {"proof"}


def test_unreachable_api_base_is_reported() -> None:
    reason = check_api_base(f"http://127.0.0.1:{_closed_port()}")
    assert reason is not None and reason.startswith("连接失败")


def _patched_client(monkeypatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    real_client = httpx.Client

    def factory(**kwargs):
        return real_client(transport=httpx.MockTransport(recording), **kwargs)

    monkeypatch.setattr(callback_check.httpx, "Client", factory)
    return seen


def test_another_instance_is_told_apart_from_this_one(monkeypatch) -> None:
    # A live agent-legion endpoint with a different per-process key.
    seen = _patched_client(
        monkeypatch, lambda request: httpx.Response(200, json={"proof": "0" * 64})
    )
    reason = check_api_base("http://127.0.0.1:8021")
    assert reason == "该地址响应来自另一个 Agent Legion 实例"
    # Token-less: the probe never carries an Authorization header.
    assert all("authorization" not in request.headers for request in seen)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(401, json={"detail": "x"}), "HTTP 401"),
        (httpx.Response(200, text="not json"), "另一个"),
        (httpx.Response(200, json=["proof"]), "另一个"),
    ],
)
def test_non_probe_answers_are_reported(monkeypatch, response, expected) -> None:
    _patched_client(monkeypatch, lambda request: response)
    reason = check_api_base("http://127.0.0.1:8000")
    assert reason is not None and expected in reason


def test_result_is_cached_per_api_base(monkeypatch) -> None:
    seen = _patched_client(monkeypatch, lambda request: httpx.Response(401))
    check_api_base("http://127.0.0.1:8000")
    check_api_base("http://127.0.0.1:8000")
    assert len(seen) == 1
    check_api_base("http://127.0.0.1:8001")
    assert len(seen) == 2


def test_detail_points_to_the_settings_entry() -> None:
    detail = unreachable_detail("http://127.0.0.1:8000", "连接失败（ConnectError）")
    assert "http://127.0.0.1:8000" in detail
    assert "全局设置 → Studio Agent 管理 → 平台回调地址" in detail

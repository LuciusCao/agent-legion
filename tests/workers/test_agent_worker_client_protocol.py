"""Host client protocol tests for the Agent Worker (worker.host.client /
heartbeat_ops / registration, #352 rebase split).

claim/heartbeat（单条 + #352 批量）/report 的 Host 协议面与注册重试从
tests/workers/test_agent_worker.py 拆出（rebase 到 0.7.0 后该文件超过
tests 1000 行预算；拆分遵循 0.7.0 已有的 test_agent_worker_service.py
先例——按测试对象分文件，不改任何断言语义）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
import requests

from worker import executor as agent_worker
from worker.registration.retry import register_with_retry


def test_client_claim_raises_auth_error_on_409() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (409, b"unknown or revoked Agent Worker")  # type: ignore[method-assign]
    with pytest.raises(agent_worker.WorkerAuthError):
        client.claim("w1")


def test_client_heartbeat_returns_status() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (409, b"")  # type: ignore[method-assign]
    assert client.heartbeat("exec-1", "lease-1") == (409, [])


def test_client_heartbeat_parses_protocol_v2_cancel_body() -> None:
    # 批次 2：v2 Host 的 heartbeat 应答 200 + 取消列表；v1 的 204 无 body。
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (  # type: ignore[method-assign]
        200,
        b'{"cancelled_execution_ids": ["exec-9", "exec-10"]}',
    )
    assert client.heartbeat("exec-1", "lease-1") == (200, ["exec-9", "exec-10"])
    client.request = lambda *a, **k: (204, b"")  # type: ignore[method-assign]
    assert client.heartbeat("exec-1", "lease-1") == (204, [])


def test_client_claim_declares_live_capacity() -> None:
    client = agent_worker.Client("http://unused")
    seen: list[dict] = []
    client.request = lambda *a, **k: (seen.append(json.loads(k["data"])), (204, b""))[1]  # type: ignore[method-assign]

    assert client.claim("w1", 70) is None

    assert seen == [{"worker_id": "w1", "max_concurrency": 70}]


def test_client_claim_declares_code_capacity() -> None:
    # 批次 2：每次 poll 重声明 code 池容量（Host 记录并强制）。
    client = agent_worker.Client("http://unused")
    seen: list[dict] = []
    client.request = lambda *a, **k: (seen.append(json.loads(k["data"])), (204, b""))[1]  # type: ignore[method-assign]

    assert client.claim("w1", 70, 4) is None

    assert seen == [{"worker_id": "w1", "max_concurrency": 70, "max_code_concurrency": 4}]


def test_client_registration_declares_latest_protocol_and_code_capacity() -> None:
    client = agent_worker.Client("http://unused")
    seen: list[dict] = []
    headers: dict[str, str] = {}

    def stub(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(json.loads(kwargs["data"]))
        headers.update(kwargs["headers"])
        return (
            201,
            b'{"worker_token": "tok", "host_protocol_version": 5, "allowed_workspaces": []}',
        )

    client.request = stub  # type: ignore[method-assign]

    client.register(
        {
            "worker_id": "w1",
            "runtimes": ["velites"],
            "max_concurrency": 1,
            "max_code_concurrency": 3,
            # #381 版本握手：prepare_runtime_models 产出的映射必须原样进
            # payload（informational 字段无守卫，改名/漏传会静默降级为 {}，
            # 此断言钉住接线——subagent 二轮评审 P3-1）。
            "runtime_versions": {"velites": "velites 0.4.0-alpha"},
        },
        ["token-a", "token-b"],
    )

    assert seen[0]["protocol_version"] == 5
    assert seen[0]["max_code_concurrency"] == 3
    assert seen[0]["runtime_versions"] == {"velites": "velites 0.4.0-alpha"}
    # issue #35：全部 scoped token 逗号拼进同一个注册请求。
    assert headers["X-Agent-Worker-Register-Tokens"] == "token-a,token-b"


def test_client_registration_fails_closed_against_v3_host() -> None:
    """#338：v4 worker 对只懂 v3 的旧 Host 拒绝注册（升级顺序：先 Host 后 Worker）。"""
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (  # type: ignore[method-assign]
        201,
        b'{"worker_token": "tok", "host_protocol_version": 3, "allowed_workspaces": []}',
    )

    with pytest.raises(agent_worker.WorkerAuthError, match="upgrade Host before Worker"):
        client.register(
            {"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1},
            ["token-a"],
        )


def test_client_registration_rejects_empty_token_list() -> None:
    client = agent_worker.Client("http://unused")
    with pytest.raises(agent_worker.WorkerAuthError, match="no register token"):
        client.register({"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1}, [])


def test_client_registration_rejects_old_host_before_claiming() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (  # type: ignore[method-assign]
        201,
        b'{"worker_token": "old-host-token", "allowed_workspaces": []}',
    )

    with pytest.raises(agent_worker.WorkerAuthError, match="upgrade Host before Worker"):
        client.register(
            {"worker_id": "w1", "runtimes": ["pi", "velites"], "max_concurrency": 1},
            ["management-token"],
        )

    assert client.token == ""


def test_client_registration_rejects_permanent_http_errors() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (401, b"bad token")  # type: ignore[method-assign]
    with pytest.raises(agent_worker.WorkerAuthError, match="registration rejected"):
        client.register(
            {"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1},
            ["bad-token"],
        )


def test_registration_retries_transient_host_errors_without_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = agent_worker.Client("http://unused")
    calls = 0

    def flaky_register(config: dict, token: str) -> dict:
        nonlocal calls
        del config, token
        calls += 1
        if calls < 3:
            # The transport-level failure register_with_retry treats as
            # "Host temporarily unavailable" (requests raises RequestException
            # subclasses; arbitrary exceptions are NOT retried anymore).
            raise requests.ConnectionError("host unavailable")
        return {"worker_token": "worker-token", "workspaces": []}

    client.register = flaky_register  # type: ignore[method-assign]
    assert register_with_retry(client, {}, ["token"], threading.Event(), 0.001)
    output = capsys.readouterr().out
    assert calls == 3
    assert "retrying" in output
    assert "Traceback" not in output


def test_registration_retries_transient_http_status_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """5xx/429 answers surface as TransientHostError and stay in the retry loop."""
    client = agent_worker.Client("http://unused")
    statuses = [503, 429, 201]

    def flaky_request(*args: object, **kwargs: object) -> tuple[int, bytes]:
        del args, kwargs
        status = statuses.pop(0)
        if status == 201:
            return (status, b'{"worker_token": "tok", "host_protocol_version": 5}')
        return (status, b"temporarily unavailable")

    client.request = flaky_request  # type: ignore[method-assign]
    config = {"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1}
    assert register_with_retry(client, config, ["token"], threading.Event(), 0.001)
    output = capsys.readouterr().out
    assert "retrying" in output
    assert "HTTP 503" in output


def test_registration_unexpected_client_error_crashes_loudly() -> None:
    """A non-retriable unexpected status (e.g. 404) must not enter the loop."""
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (404, b"not found")  # type: ignore[method-assign]
    config = {"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1}
    with pytest.raises(RuntimeError, match="HTTP 404"):
        register_with_retry(client, config, ["token"], threading.Event(), 0.001)


def test_client_heartbeat_and_report_send_lease_header() -> None:
    client = agent_worker.Client("http://unused")
    seen: list[dict] = []
    client.request = lambda *a, **k: (seen.append(k.get("headers") or {}), (204, b""))[1]  # type: ignore[method-assign]
    client.heartbeat("exec-1", "lease-9")
    archive = Path(__file__)
    client.report("exec-1", "lease-9", {"status": "completed"}, archive)
    assert [call.get("X-Agent-Lease-Id") for call in seen] == ["lease-9", "lease-9"]


# --- #352: 批量心跳 client 面 -------------------------------------------------


def test_client_heartbeat_batch_posts_executions_and_parses_body() -> None:
    client = agent_worker.Client("http://unused")
    seen: list[dict] = []
    client.request = lambda *a, **k: (  # type: ignore[method-assign]
        seen.append({"method": a[0], "path": a[1], "data": json.loads(k["data"])}),
        (
            200,
            b'{"renewed": ["exec-1"], "lost": ["exec-2"], "cancelled_execution_ids": ["exec-3"]}',
        ),
    )[1]

    outcome = client.heartbeat_batch([("exec-1", "lease-1"), ("exec-2", "lease-2")])

    assert outcome == (
        200,
        {"renewed": ["exec-1"], "lost": ["exec-2"], "cancelled_execution_ids": ["exec-3"]},
    )
    assert seen == [
        {
            "method": "POST",
            "path": "/api/agent-executions/heartbeats",
            "data": {
                "executions": [
                    {"execution_id": "exec-1", "lease_id": "lease-1"},
                    {"execution_id": "exec-2", "lease_id": "lease-2"},
                ]
            },
        }
    ]


def test_client_heartbeat_batch_returns_none_on_missing_endpoint() -> None:
    """pre-v5 Host 404/405 → None，调用方据此降级为逐执行心跳。"""
    client = agent_worker.Client("http://used")
    client.request = lambda *a, **k: (404, b"not found")  # type: ignore[method-assign]
    assert client.heartbeat_batch([("exec-1", "lease-1")]) is None
    client.request = lambda *a, **k: (405, b"method not allowed")  # type: ignore[method-assign]
    assert client.heartbeat_batch([]) is None


def test_client_heartbeat_batch_degrade_logs_lease_scale(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """>#5125358408 P2：降级到单拍时打一条规模提示——N × 5s 是滚动升级窗口
    内该 tick 的墙钟上限，让运维看到 N 而不是只有「降级了」。"""
    with caplog.at_level("INFO", logger="worker.host.heartbeat_ops"):
        client = agent_worker.Client("http://unused")
        client.request = lambda *a, **k: (404, b"not found")  # type: ignore[method-assign]
        assert (
            client.heartbeat_batch([("exec-1", "l1"), ("exec-2", "l2"), ("exec-3", "l3")]) is None
        )
    matches = [r for r in caplog.records if "degraded to single beats" in r.getMessage()]
    assert len(matches) == 1
    message = matches[0].getMessage()
    assert "3 leases" in message
    assert "5s" in message  # the per-tick ceiling: N × SINGLE_BEAT_TIMEOUT_SECONDS


def test_client_heartbeat_batch_rejects_error_status() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (500, b"boom")  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="batch heartbeat failed: HTTP 500"):
        client.heartbeat_batch([("exec-1", "lease-1")])

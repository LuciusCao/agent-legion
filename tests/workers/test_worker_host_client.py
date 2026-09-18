"""Unit tests for the Worker Host client (worker/host/client.py + worker/host/transfer.py)."""

from __future__ import annotations

import http.server
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi import FastAPI, Request

from worker.host.client import Client, WorkerAuthError
from worker.host.transfer import HostRequestError


def _artifact(tmp_path: Path) -> Path:
    path = tmp_path / "out.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    return sleeps


def test_upload_artifact_succeeds_first_try(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = {"n": 0}

    def fake_request(*args, **kwargs):
        calls["n"] += 1
        return 201, json.dumps({"hash": "abc"}).encode()

    monkeypatch.setattr(Client, "request", fake_request)
    assert Client("http://host").upload_artifact(_artifact(tmp_path)) == "sha256:abc"
    assert calls["n"] == 1


def test_upload_artifact_retries_5xx_with_backoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps = _patch_sleep(monkeypatch)
    responses = iter([(500, b"err"), (502, b"err"), (201, json.dumps({"hash": "abc"}).encode())])
    monkeypatch.setattr(Client, "request", lambda *a, **k: next(responses))
    assert Client("http://host").upload_artifact(_artifact(tmp_path)) == "sha256:abc"
    # full jitter：等待落在 [0, backoff]，上限仍按 2x 推进。
    assert len(sleeps) == 2
    assert all(0 <= sleep <= cap for sleep, cap in zip(sleeps, [1.0, 2.0], strict=True))


def test_upload_artifact_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(Client, "request", lambda *a, **k: (500, b"err"))
    with pytest.raises(RuntimeError, match="artifact upload failed: HTTP 500"):
        Client("http://host").upload_artifact(_artifact(tmp_path))
    assert len(sleeps) == 2  # 3 attempts, 2 backoff sleeps


def test_upload_artifact_does_not_retry_4xx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(Client, "request", lambda *a, **k: (413, b"too large"))
    with pytest.raises(RuntimeError, match="artifact upload failed: HTTP 413"):
        Client("http://host").upload_artifact(_artifact(tmp_path))
    assert sleeps == []


def test_upload_artifact_retries_connection_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps = _patch_sleep(monkeypatch)
    responses = iter(
        [
            requests.ConnectionError("connection reset"),
            (201, json.dumps({"hash": "abc"}).encode()),
        ]
    )

    def fake_request(*args, **kwargs):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(Client, "request", fake_request)
    assert Client("http://host").upload_artifact(_artifact(tmp_path)) == "sha256:abc"
    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= 1.0


def test_upload_artifact_reports_last_connection_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_sleep(monkeypatch)

    def fake_request(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(Client, "request", fake_request)
    with pytest.raises(RuntimeError, match="artifact upload failed: .*connection refused"):
        Client("http://host").upload_artifact(_artifact(tmp_path))


def test_get_self_uses_worker_token_and_returns_own_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []

    def fake_request(self, method: str, path: str, **kwargs) -> tuple[int, bytes]:
        seen.append((method, path))
        return 200, b'{"worker_id":"worker-1","name":"Worker 1"}'

    monkeypatch.setattr(Client, "request", fake_request)

    assert Client("http://host", "worker-token").get_self()["worker_id"] == "worker-1"
    assert seen == [("GET", "/api/agent-workers/self")]


def test_get_self_rejects_invalid_worker_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: (401, b"invalid token"))

    with pytest.raises(WorkerAuthError):
        Client("http://host", "bad-token").get_self()


def test_get_ops_metrics_uses_worker_scoped_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []

    def fake_request(self, method: str, path: str, **kwargs) -> tuple[int, bytes]:
        seen.append((method, path))
        return 200, b'{"granularity":"6h","buckets":[]}'

    monkeypatch.setattr(Client, "request", fake_request)

    payload = Client("http://host", "worker-token").get_ops_metrics("6h")

    assert payload["granularity"] == "6h"
    assert seen == [("GET", "/api/agent-workers/self/metrics?granularity=6h")]


def test_get_ops_metrics_rejects_invalid_worker_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: (401, b"invalid token"))

    with pytest.raises(WorkerAuthError):
        Client("http://host", "bad-token").get_ops_metrics("24h")


def test_upload_artifact_opens_fresh_stream_per_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Payload streams from disk; a retry must get a re-opened stream, not an
    exhausted one."""
    _patch_sleep(monkeypatch)
    seen: list[bytes] = []

    def fake_request(*args, **kwargs):
        seen.append(kwargs["data"].read())
        if len(seen) < 2:
            raise requests.ConnectionError("reset mid-upload")
        return 201, json.dumps({"hash": "abc"}).encode()

    monkeypatch.setattr(Client, "request", fake_request)
    assert Client("http://host").upload_artifact(_artifact(tmp_path)) == "sha256:abc"
    assert seen == [b"{}", b"{}"]


def test_report_streams_archive_from_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archive = tmp_path / "result.tar.gz"
    archive.write_bytes(b"archive-bytes")

    def fake_request(*args, **kwargs):
        assert kwargs["data"].read() == b"archive-bytes"
        return 204, b""

    monkeypatch.setattr(Client, "request", fake_request)
    assert Client("http://host").report("exec-1", "lease-1", {}, archive) == (204, b"")


def test_download_writes_response_to_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_request(*args, **kwargs):
        destination = kwargs["stream_to"]
        assert destination == tmp_path / "bundle.tar.gz"
        destination.write_bytes(b"bundle-bytes")  # 模拟 request 的流式落盘契约
        return 200, b""

    monkeypatch.setattr(Client, "request", fake_request)
    target = tmp_path / "bundle.tar.gz"
    Client("http://host").download("/api/x/bundle", target)
    assert target.read_bytes() == b"bundle-bytes"


def test_download_4xx_is_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Client, "request", lambda *a, **k: (404, b"nope"))
    with pytest.raises(HostRequestError, match="HTTP 404"):
        Client("http://host").download("/api/x/bundle", tmp_path / "bundle.tar.gz")


def test_request_stream_to_writes_body_atomically(tmp_path: Path) -> None:
    """End-to-end over a real local HTTP server: a multi-MB body lands in the
    destination via temp file + atomic rename, never buffered whole."""
    body = b"x" * (2 << 20)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = Client(f"http://127.0.0.1:{server.server_port}")
        target = tmp_path / "bundle.tar.gz"
        status, content = client.request("GET", "/bundle", stream_to=target)
        assert (status, content) == (200, b"")
        assert target.read_bytes() == body
        assert list(tmp_path.glob("*.part")) == []
    finally:
        server.shutdown()
        server.server_close()


def test_download_retries_mid_stream_connection_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """iter_content 中途断连（requests 已把 urllib3 异常包装成 RequestException）
    必须进入重试路径；重试重新截断 .part，最终内容完整。"""
    sleeps = _patch_sleep(monkeypatch)
    body = b"stream-bytes"
    attempts = {"n": 0}

    class FakeResponse:
        status_code = 200

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def iter_content(self, chunk_size: int = 1) -> Any:
            attempts["n"] += 1
            yield body[:4]
            if attempts["n"] == 1:
                raise requests.ConnectionError("connection reset mid-stream")
            yield body[4:]

    client = Client("http://host")
    monkeypatch.setattr(client.session, "request", lambda *a, **k: FakeResponse())
    target = tmp_path / "bundle.tar.gz"

    client.download("/api/x/bundle", target)

    assert attempts["n"] == 2  # 第一次中断后真的重试了
    assert target.read_bytes() == body  # 重试截断重写，而非追加半截
    assert list(tmp_path.glob("*.part")) == []
    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= 1.0


# -- #748 review P2：X-Agent-Result 头的 CJK 膨胀——序列化选型与真链路回读 --


def test_result_header_value_escapes_cjk_as_raw_utf8_bytes() -> None:
    """选型证据：ensure_ascii=False + UTF-8 字节。4000 个 CJK 字符的 tail
    序列化后必须 ~12KB（真 CJK 3 字节/字），而不是转义形态的 ~24KB——
    转义形态会撞破 h11 的 16KB 单事件上限，让结果永久不可投递。"""
    from worker.host.transfer import _RESULT_HEADER_BUDGET, _result_header_value

    metadata = {
        "status": "failed",
        "exit_code": 1,
        "error_message": "Agent process exited 1: 任务失败",
        "command": ["pi"],
        "output_artifacts": {},
        "run_dir": "runs/node_a/worker",
        "agent_stderr_tail": "错误" * 2000,  # 4000 个 CJK 字符
    }
    header = _result_header_value(metadata)
    # 转义形态 ~24KB；非转义 + 字节预算 < 14KB 预算。
    assert len(header) < _RESULT_HEADER_BUDGET
    assert len(header) < 16 * 1024  # h11 max_incomplete_event 余量内
    # 内容不丢骨架：CJK 原样（非 \uXXXX），error_message 完整保留。
    decoded = json.loads(header.decode("utf-8"))
    assert decoded["error_message"] == "Agent process exited 1: 任务失败"
    assert "\\u" not in header.decode("utf-8")


def test_result_header_value_shrinks_oversized_tail_under_budget() -> None:
    """超预算时按 tail 优先收缩（error_message 是分类面，最后动）：收缩后
    必须落在预算内，且 error_message 一字不动。"""
    from worker.host.transfer import _RESULT_HEADER_BUDGET, _result_header_value

    metadata = {
        "status": "failed",
        "exit_code": 1,
        "error_message": "Agent process exited 1: ValueError: boom",
        "agent_stderr_tail": "错" * 8000 + "x" * 2000,  # 远超预算
    }
    header = _result_header_value(metadata)
    assert len(header) <= _RESULT_HEADER_BUDGET
    decoded = json.loads(header.decode("utf-8"))
    assert decoded["error_message"] == "Agent process exited 1: ValueError: boom"
    assert len(decoded["agent_stderr_tail"]) > 0


def test_result_header_value_ascii_metadata_unchanged() -> None:
    """纯 ASCII metadata 的序列化与旧 ensure_ascii=True 形态逐字节一致——
    旧 Worker/Host 兼容面不动。"""
    from worker.host.transfer import _result_header_value

    metadata = {"status": "failed", "exit_code": 3, "error_message": "x"}
    assert _result_header_value(metadata) == json.dumps(metadata).encode()


def _direct_ref(i: int) -> dict:
    # #160 D12 直传产物 ref 形态（worker/artifact/upload.py 的返回值）。
    return {
        "storage_key": f"jobs-staging/ws-1/job-1/exec-1/output-{i:03d}.json",
        "size_bytes": 12345,
        "content_hash": "a" * 64,
    }


def test_result_header_value_truncates_128_direct_refs_under_budget() -> None:
    """#748 R2 P2-1：128 个直传 ref（Host 侧 _MAX_OUTPUT_ARTIFACTS 上限）全 ref
    形态 ~25KB，撞破 14KB 预算——成功运行同样中招（成功上报也带产物清单）。
    多级降级第三级：截断 output_artifacts 为前缀 + 截断标记；序列化字节必须
    落在预算内，保留的 ref 逐字节原样（前缀，不是改写）。"""
    from worker.host.transfer import _RESULT_HEADER_BUDGET, _result_header_value

    artifacts = {f"output-{i:03d}.json": _direct_ref(i) for i in range(128)}
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "error_message": "",
        "command": ["pi"],
        "output_artifacts": artifacts,
        "run_dir": "runs/node_a/worker",
    }
    header = _result_header_value(metadata)
    assert len(header) <= _RESULT_HEADER_BUDGET
    decoded = json.loads(header.decode("utf-8"))
    kept = decoded["output_artifacts"]
    assert 0 < len(kept) < 128
    # 前缀保序：保留的是前 N 个，ref 内容逐字节未动。
    kept_names = list(kept)
    assert kept_names == [f"output-{i:03d}.json" for i in range(len(kept))]
    for name, ref in kept.items():
        assert ref == artifacts[name]
    # 截断标记：truncated=true + 原数量。
    assert decoded["output_artifacts_truncated"] is True
    assert decoded["output_artifacts_total"] == 128


def test_result_header_value_degrades_giant_refs_to_empty_with_markers() -> None:
    """降级到极致：单条 ref 自身就超预算（超长 storage_key）时，清单整体
    降级为空列表 + 截断标记——头仍可投递（这是本修复的存活底线），产物
    字节仍在归档里（直传 ref 丢失面是「回退归档通道」而非数据丢失）。"""
    from worker.host.transfer import _RESULT_HEADER_BUDGET, _result_header_value

    giant = {
        "storage_key": "jobs-staging/" + "x" * 20_000,
        "size_bytes": 1,
        "content_hash": "a" * 64,
    }
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "error_message": "",
        "command": ["pi"],
        "output_artifacts": {"a.json": giant},
        "run_dir": "runs/node_a/worker",
    }
    header = _result_header_value(metadata)
    assert len(header) <= _RESULT_HEADER_BUDGET
    decoded = json.loads(header.decode("utf-8"))
    assert decoded["output_artifacts"] == {}
    assert decoded["output_artifacts_truncated"] is True
    assert decoded["output_artifacts_total"] == 1


def test_result_header_value_truncates_after_tail_and_error_shrink() -> None:
    """多级顺序：tail 先缩、error_message 次之、产物清单最后——三面同时
    超预算时前两级先收敛（分类面优先于产物清单之前保住）。"""
    from worker.host.transfer import _RESULT_HEADER_BUDGET, _result_header_value

    artifacts = {f"output-{i:03d}.json": _direct_ref(i) for i in range(128)}
    metadata = {
        "status": "failed",
        "exit_code": 1,
        "error_message": "Agent process exited 1: ValueError: boom",
        "command": ["pi"],
        "output_artifacts": artifacts,
        "run_dir": "runs/node_a/worker",
        "agent_stderr_tail": "错" * 8000 + "x" * 2000,
    }
    header = _result_header_value(metadata)
    assert len(header) <= _RESULT_HEADER_BUDGET
    decoded = json.loads(header.decode("utf-8"))
    # error_message 是分类面：第三级介入前必须完整。
    assert decoded["error_message"] == "Agent process exited 1: ValueError: boom"
    assert decoded["output_artifacts_truncated"] is True
    assert len(decoded["output_artifacts"]) < 128


def test_cjk_result_header_roundtrips_through_real_h11_uvicorn_starlette() -> None:
    """真链路验证（#748 review P2 选型依据）：requests(字节头) → h11 →
    uvicorn → Starlette latin-1 解码 → Host 侧 _recover_result_header 反解。
    4000 字 CJK tail 在真实 uvicorn+h11 服务上原样读回——非 ASCII 头不被
    拒收，json.loads 后逐字段相等。"""
    import uvicorn

    from server.app.routes.agent_worker_results import _recover_result_header
    from worker.host.transfer import _result_header_value

    metadata = {
        "status": "failed",
        "exit_code": 3,
        "error_message": "Agent process exited 3: 任务执行失败",
        "command": ["pi"],
        "output_artifacts": {},
        "run_dir": "runs/node_a/worker",
        "agent_stderr_tail": "追踪" * 2000,  # 4000 个 CJK 字符
    }
    header_bytes = _result_header_value(metadata)
    assert len(header_bytes) > 11 * 1024  # 真实的大头场景（4k CJK 字 ~12KB）
    seen: dict[str, str] = {}

    app = FastAPI()

    @app.post("/result")
    async def result(request: Request) -> object:
        raw = request.headers.get("x-agent-result", "")
        seen["latin1"] = raw
        seen["recovered"] = _recover_result_header(raw)
        return {"ok": True}

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    port = None
    for _ in range(200):
        sockets = getattr(server, "servers", None)
        if sockets:
            port = sockets[0].sockets[0].getsockname()[1]
            break
        time.sleep(0.05)
    assert port is not None, "uvicorn did not start"
    try:
        response = requests.post(
            f"http://127.0.0.1:{port}/result",
            data=b"",
            headers={"X-Agent-Result": header_bytes, "X-Agent-Lease-Id": "lease-1"},
            timeout=10,
        )
        assert response.status_code == 200, response.text
        # Starlette 交出来的是 latin-1 解码视图（mojibake 形态）。
        assert seen["latin1"] != metadata["agent_stderr_tail"][:100]
        # 反解后逐字段还原。
        assert json.loads(seen["recovered"]) == metadata
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_recover_result_header_keeps_legacy_ascii_and_mojibake_as_is() -> None:
    """Host 侧反解的兜底：纯 ASCII（新旧编码同形）原样；已破坏（非合法
    UTF-8 的 latin-1 序列）不炸、原样透传。"""
    from server.app.routes.agent_worker_results import _recover_result_header

    assert _recover_result_header('{"status": "failed"}') == '{"status": "failed"}'
    # 单字节 latin-1 扩展区（é = U+00E9）不是合法 UTF-8 多字节序列的起点
    # 之列时保持原样——不抛错、不改写。
    raw = "caf\xe9"
    assert _recover_result_header(raw) == raw

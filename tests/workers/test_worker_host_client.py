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
from worker.host.transfer import HostRequestError, TransferStopped


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


def test_upload_artifact_does_not_reopen_after_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lease loss during an attempt aborts the internal retry before the next open."""
    stop = threading.Event()
    seen: list[bytes] = []

    def fake_request(*args, **kwargs):
        seen.append(kwargs["data"].read())
        stop.set()
        raise requests.ConnectionError("lost while uploading")

    monkeypatch.setattr(Client, "request", fake_request)
    with pytest.raises(TransferStopped):
        Client("http://host").upload_artifact(_artifact(tmp_path), stop=stop)
    assert seen == [b"{}"]


def test_report_streams_archive_from_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archive = tmp_path / "result.tar.gz"
    archive.write_bytes(b"archive-bytes")

    def fake_request(*args, **kwargs):
        assert kwargs["data"].read() == b"archive-bytes"
        return 204, b""

    monkeypatch.setattr(Client, "request", fake_request)
    assert Client("http://host").report("exec-1", "lease-1", {}, archive) == (204, b"")


def test_report_does_not_reopen_archive_after_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stop = threading.Event()
    archive = tmp_path / "result.tar.gz"
    archive.write_bytes(b"old-attempt")
    seen: list[bytes] = []

    def fake_request(*args, **kwargs):
        seen.append(kwargs["data"].read())
        stop.set()
        raise requests.ConnectionError("lost while reporting")

    monkeypatch.setattr(Client, "request", fake_request)
    with pytest.raises(TransferStopped):
        Client("http://host").report("exec-1", "lease-1", {}, archive, stop=stop)
    assert seen == [b"old-attempt"]


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
    必须落在预算内，且 error_message 一字不动。产物清单为空（成功直传前
    的元数据骨架），不触发第三级。#755 终审 P2-2：收缩保尾不保头——崩溃
    栈在流末尾，头截会在满 tail 时把崩溃头整体丢掉。"""
    from worker.host.transfer import _RESULT_HEADER_BUDGET, _result_header_value

    metadata = {
        "status": "failed",
        "exit_code": 1,
        "error_message": "Agent process exited 1: ValueError: boom",
        "agent_stderr_tail": "错" * 8000 + "x" * 2000,  # 远超预算
        "output_artifacts": {},
    }
    header = _result_header_value(metadata)
    assert len(header) <= _RESULT_HEADER_BUDGET
    decoded = json.loads(header.decode("utf-8"))
    assert decoded["error_message"] == "Agent process exited 1: ValueError: boom"
    assert len(decoded["agent_stderr_tail"]) > 0
    # 保尾：末段（崩溃头所在的尾部）完整保留，丢的是头部噪音。
    assert decoded["agent_stderr_tail"].endswith("x" * 2000)


def test_result_header_value_stage_order_tail_error_then_artifact_signal() -> None:
    """多级顺序：tail 先缩、error_message 次之、command（纯观测面）再次、
    产物清单最后——各面同时超预算时前三级先收敛（分类面与产物清单都在
    观测面之前保住），收敛后清单仍放不下时才抛回退信号（不是截断）。"""
    from worker.host.transfer import ResultHeaderOverflow, _result_header_value

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
    with pytest.raises(ResultHeaderOverflow):
        _result_header_value(metadata)
    # 前两级是真收缩（可观察面）：同载荷去掉产物清单后，tail/error 收敛
    # 落预算且 error_message 完整——证明信号只在两级收缩之后才触发。
    shrunk = dict(metadata, output_artifacts={})
    decoded = json.loads(_result_header_value(shrunk).decode("utf-8"))
    assert decoded["error_message"] == "Agent process exited 1: ValueError: boom"
    assert len(decoded["agent_stderr_tail"]) > 0


def test_result_header_value_drops_command_before_artifact_list() -> None:
    """#755 对抗复审 P2-1b：command 是纯观测面（Host 只记录不判定）——动产
    物清单之前先清空它。128 条 CAS 引用 + --require-output 重复的巨型 argv
    （~7.7KB）：清空 command 后清单整体落预算，产物引用一条不丢、无截断
    标记。"""
    from worker.host.transfer import _RESULT_HEADER_BUDGET, _result_header_value

    artifacts = {f"output-{i:03d}.json": f"sha256:{'a' * 64}" for i in range(128)}
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "error_message": "",
        "command": ["pi"] + [f"--require-output=output-{i:03d}.json" for i in range(128)],
        "output_artifacts": artifacts,
        "run_dir": "runs/node_a/worker",
    }
    header = _result_header_value(metadata)
    assert len(header) <= _RESULT_HEADER_BUDGET
    decoded = json.loads(header.decode("utf-8"))
    assert decoded["command"] == []  # 观测面已降级
    assert decoded["output_artifacts"] == artifacts  # 产物清单一字不丢
    assert "output_artifacts_truncated" not in decoded
    assert "output_artifacts_total" not in decoded


def test_result_header_value_giant_direct_ref_still_signals_fallback() -> None:
    """单条 ref 自身就超预算（超长 storage_key）的直传形态：同样抛回退信号
    而非降级为空清单——回退后 CAS 形态（或 prepare 降级失败形态）才是
    最后手段截断的入口，头永不因直传形态而直接不可投递。"""
    from worker.host.transfer import ResultHeaderOverflow, _result_header_value

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
    with pytest.raises(ResultHeaderOverflow):
        _result_header_value(metadata)


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


def test_result_header_value_signals_fallback_for_128_direct_refs() -> None:
    """#748 R3（codex review P1）：128 个直传 ref（Host 侧 _MAX_OUTPUT_ARTIFACTS
    上限）全 ref 形态 ~25KB，撞破 14KB 预算——成功运行同样中招（成功上报也带
    产物清单）。修复前第三级截断为前缀 + 标记，但直传模式的归档不带产物字节、
    Host 也不用截断标记恢复引用——前缀之外的产物进不了 job_dir，成功的执行被
    改判 Missing outputs。修复后该形态抛 ResultHeaderOverflow 回退信号：上传
    队列清空直传规格重跑 prepare（归档内嵌模式），引用回到 CAS 形态。"""
    from worker.host.transfer import ResultHeaderOverflow, _result_header_value

    artifacts = {f"output-{i:03d}.json": _direct_ref(i) for i in range(128)}
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "error_message": "",
        "command": ["pi"],
        "output_artifacts": artifacts,
        "run_dir": "runs/node_a/worker",
    }
    with pytest.raises(ResultHeaderOverflow, match="archive-embed fallback"):
        _result_header_value(metadata)
    # 输入 dict 不被信号破坏（回退重备用的是原始 metadata）。
    assert metadata["output_artifacts"] == artifacts


def test_result_header_value_cas_refs_fit_budget_without_truncation() -> None:
    """#748 R3（codex review P1）回退终点：归档内嵌模式下引用是 CAS 字符串
    （~78B/条），128 条全量 ~12KB 天然落预算——无需任何截断/标记，全部产物
    引用完整上报（codex 指出的问题形态在回退后彻底消失）。"""
    from worker.host.transfer import _RESULT_HEADER_BUDGET, _result_header_value

    artifacts = {f"output-{i:03d}.json": f"sha256:{'a' * 64}" for i in range(128)}
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
    assert decoded["output_artifacts"] == artifacts  # 128 条全量、逐字节原样
    assert "output_artifacts_truncated" not in decoded
    assert "output_artifacts_total" not in decoded


def test_result_header_value_last_resort_truncates_cas_refs_to_empty() -> None:
    """最后手段兜底（#755 后仅剩的清单不可缩形态：超长产物名的 CAS 清单）：
    command 面已先清空（纯观测面），清单仍超预算时降级为空 + 截断标记。
    标记语义（#755 对抗复审 P2-1a）：Host 见 truncated 跳过「空清单改判
    failed」，从归档暂存视图判定 produced/missing（CAS 形态产物字节本来
    就在归档里）；标记仍不用于恢复直传 ref。total 只 stamp 一次（128，
    不是逐趟漂移后的残值）。截断后载荷回落预算内——R3 时代的巨型
    command 残差面已随 command 降级阶段消失。"""
    from worker.host.transfer import _RESULT_HEADER_BUDGET, _result_header_value

    artifacts = {f"outputs/{i:03d}/" + "n" * 80 + ".json": f"sha256:{'a' * 64}" for i in range(128)}
    metadata = {
        "status": "completed",
        "exit_code": 0,
        "error_message": "",
        "command": ["pi"],
        "output_artifacts": artifacts,
        "run_dir": "runs/node_a/worker",
    }
    header = _result_header_value(metadata)
    assert len(header) <= _RESULT_HEADER_BUDGET  # 截断后回落预算内
    decoded = json.loads(header.decode("utf-8"))
    # 清单被整体降级为空（最后手段），标记 + 一次性 total；command 面已先降级。
    assert decoded["command"] == []
    assert decoded["output_artifacts"] == {}
    assert decoded["output_artifacts_truncated"] is True
    assert decoded["output_artifacts_total"] == 128


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

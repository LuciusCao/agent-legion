"""HTTP client for the Worker's pull protocol: control calls (register/claim/
heartbeat/metrics) live here; retried bulk transfers come from the
``TransferOperations`` mixin in ``worker.host.transfer``.
"""

from __future__ import annotations

import contextlib
import json
import urllib.parse
from pathlib import Path
from typing import Any, BinaryIO

import requests

from shared.protocol import PROTOCOL_VERSION
from worker.host.errors import TransientHostError, WorkerAuthError
from worker.host.transfer import DEFAULT_TRANSFER_TIMEOUT, TransferOperations

# Protocol history lives in shared/protocol.py (shipped in the worker image,
# imported by both sides): v3 = runtime-scoped model declarations. A v3
# Worker must fail closed against an older Host that erases model runtimes.
# Registration negotiates this version with the Host.

DEFAULT_TIMEOUT = 30

__all__ = ["Client", "TransientHostError", "WorkerAuthError"]


class Client(TransferOperations):
    def __init__(
        self,
        host: str,
        token: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        transfer_timeout: float = DEFAULT_TRANSFER_TIMEOUT,
    ) -> None:
        self.host = host.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.transfer_timeout = transfer_timeout
        # Session 复用 TCP 连接：lease 心跳 15s 一次、claim 轮询 2s 一次，
        # keep-alive 避免每次调用都重新握手。
        self.session = requests.Session()

    def request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | BinaryIO | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        stream_to: Path | None = None,
    ) -> tuple[int, bytes]:
        response = self.session.request(
            method,
            f"{self.host}{path}",
            data=data,
            headers={
                **({"X-Agent-Worker-Token": self.token} if self.token else {}),
                **(headers or {}),
            },
            timeout=self.timeout if timeout is None else timeout,
            stream=stream_to is not None,
        )
        # 大文件下载：流式写同目录临时文件再原子 rename，避免全量入内存；
        # 出错（4xx/5xx 小 body）仍读 content 供上层判断。iter_content 会把
        # urllib3 的断连/读超时包装成 RequestException，进入重试路径；重试时
        # "wb" 重新截断 .part，不会追加。残留 .part 由 clean_work_root 回收。
        if stream_to is None or response.status_code >= 400:
            return response.status_code, response.content
        temporary = stream_to.with_suffix(stream_to.suffix + ".part")
        with response, temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
        temporary.replace(stream_to)
        return response.status_code, b""

    def register(self, config: dict[str, Any], management_tokens: list[str]) -> dict[str, Any]:
        """Register with every scoped token; returns the parsed response.

        All tokens travel in one request (X-Agent-Worker-Register-Tokens,
        comma-joined); the Host resolves the union scope and rejects the whole
        registration when any token is unknown or revoked. The response
        carries worker_token plus per-workspace rows (id + name) for the
        console."""
        payload = {
            "worker_id": config["worker_id"],
            "name": config.get("name", config["worker_id"]),
            "runtimes": config["runtimes"],
            "capabilities": config.get("capabilities", []),
            "models": config.get("models", []),
            "max_concurrency": config["max_concurrency"],
            # Code-execution capacity pool (batch 2); 0/absent = agent-only.
            "max_code_concurrency": int(config.get("max_code_concurrency", 0) or 0),
            "labels": config.get("labels", {}),
            "protocol_version": PROTOCOL_VERSION,
        }
        tokens = ",".join(token for token in management_tokens if token)
        if not tokens:
            raise WorkerAuthError("Agent Worker registration rejected: no register token")
        status, body = self.request(
            "POST",
            "/api/agent-workers/register",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Agent-Worker-Register-Tokens": tokens,
            },
        )
        if status in (400, 401, 403, 409, 422):
            raise WorkerAuthError(
                f"Agent Worker registration rejected: HTTP {status}: {body[:300]!r}"
            )
        if status != 201:
            # 5xx/429 mean the Host is temporarily unhealthy (retry); any
            # other unexpected status is a routing/contract bug that must
            # crash loudly instead of disguising itself as an outage.
            message = f"Agent Worker registration failed: HTTP {status}: {body[:300]!r}"
            if status >= 500 or status == 429:
                raise TransientHostError(message)
            raise RuntimeError(message)
        document = json.loads(body)
        if int(document.get("host_protocol_version", 0)) < PROTOCOL_VERSION:
            raise WorkerAuthError(
                "Host protocol does not support runtime-scoped models; upgrade Host before Worker"
            )
        self.token = str(document["worker_token"])
        return dict(document)

    def get_self(self) -> dict[str, Any]:
        """Return this Worker's Host-side record using its per-worker token."""
        status, body = self.request("GET", "/api/agent-workers/self")
        if status in (401, 409):
            raise WorkerAuthError(f"HTTP {status}: {body[:300]!r}")
        if status != 200:
            raise RuntimeError(f"Agent Worker status failed: HTTP {status}: {body[:300]!r}")
        return dict(json.loads(body))

    def claim(
        self,
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"worker_id": worker_id}
        if max_concurrency is not None:
            payload["max_concurrency"] = max_concurrency
        if max_code_concurrency is not None:
            payload["max_code_concurrency"] = max_code_concurrency
        status, body = self.request(
            "POST",
            "/api/agent-executions/claim",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        if status == 204:
            return None
        if status in (401, 409):
            raise WorkerAuthError(f"HTTP {status}: {body[:300]!r}")
        if status != 200:
            raise RuntimeError(f"Agent claim failed: HTTP {status}: {body[:300]!r}")
        return json.loads(body)

    def get_ops_metrics(self, granularity: str) -> dict[str, Any]:
        """Fetch this Worker's metrics with its issued Worker token."""
        query = urllib.parse.urlencode({"granularity": granularity})
        status, body = self.request("GET", f"/api/agent-workers/self/metrics?{query}")
        if status in (401, 409):
            raise WorkerAuthError(f"HTTP {status}: {body[:300]!r}")
        if status != 200:
            raise RuntimeError(f"ops metrics failed: HTTP {status}: {body[:300]!r}")
        return json.loads(body)

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        """Beat once; returns (status, cancelled_execution_ids).

        Protocol v2 Hosts answer 200 with a body listing this Worker's
        cancelled kind='code' executions; v1 answers 204 (no body)."""
        status, body = self.request(
            "POST",
            f"/api/agent-executions/{execution_id}/heartbeat",
            headers={"X-Agent-Lease-Id": lease_id},
        )
        cancelled: list[str] = []
        if status == 200:
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                cancelled = [
                    str(value) for value in json.loads(body).get("cancelled_execution_ids", [])
                ]
        return status, cancelled

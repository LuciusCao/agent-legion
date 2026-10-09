"""Transfer operations for the Worker's Host client: retries, downloads, uploads.

Bulk transfers move megabytes and share the Host with every other execution;
they get a longer timeout and backoff retry on transient failures, unlike
the control calls in ``worker.host.client``.

#843 v2（PR-2）：结果上报不再携带 ``X-Agent-Result`` 头——元数据整体落在
结果归档的保留首成员 ``result.json``（由 worker/upload 的准备链写入），
请求头只带固定 ASCII 引导值 ``X-Agent-Result-Format: 2``（常量见
shared/code_contract）。#748/#755 的 14 KiB 头预算、四段降级链
（tail → error_message → command → 清单）与 ResultHeaderOverflow 换轨信号
随之退役；v2 契约明文禁止 payload 携带 ``output_artifacts_in_archive``
标记（Host 侧 v2 会显式忽略它，但 Worker 契约禁止发出）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast

import requests

from shared.code_contract import RESULT_METADATA_FORMAT_HEADER, RESULT_METADATA_FORMAT_V2
from worker._retry import StopSignal, run_with_retry

# Transient network errors (timeout/reset/refused) and Host 5xx get
# exponential backoff (1s, 2s, 4s, …). requests wraps socket timeouts as
# requests.Timeout and resets/refusals as requests.ConnectionError — both
# are RequestException subclasses — so a single 30s stall no longer kills a
# finished execution. Builtin TimeoutError/ConnectionError stay as a safety
# net for errors raised below the requests layer.
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SECONDS = 1.0
_TRANSIENT_ERRORS = (requests.RequestException, TimeoutError, ConnectionError)

DEFAULT_TRANSFER_TIMEOUT = 120


class HostRequestError(RuntimeError):
    """Terminal non-retryable Host response (4xx); ``status`` carries the code."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class _TransientTransferError(RuntimeError):
    """Internal carrier for one retried attempt's failure message."""


class TransferStopped(RuntimeError):
    """A transfer stopped before another file-open/retry attempt began."""


class TransferOperations:
    """Mixin with the retried transfer calls; the concrete client provides
    ``request`` and the timeout attributes."""

    host: str
    token: str
    timeout: float
    transfer_timeout: float

    def request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | BinaryIO | None = None,
        # 头值类型保留 str|bytes 联合：#748 的 UTF-8 字节头已随 v2 退役
        # （现行头全为 ASCII str），bytes 形态仅作 requests 传输层兼容留存。
        headers: dict[str, str | bytes] | None = None,
        timeout: float | None = None,
        stream_to: Path | None = None,
    ) -> tuple[int, bytes]:
        raise NotImplementedError

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        label: str,
        timeout: float,
        data: bytes | Callable[[], BinaryIO] | None = None,
        headers: dict[str, str | bytes] | None = None,
        stream_to: Path | None = None,
        stop: StopSignal | None = None,
        max_attempts: int = _RETRY_MAX_ATTEMPTS,
    ) -> tuple[int, bytes]:
        """Request with backoff on transient network errors and Host 5xx.

        4xx passes through unchanged (a verdict, not a transient condition);
        exhaustion raises RuntimeError with the call-site label. A callable
        ``data`` is invoked per attempt so upload streams are re-opened on
        retry; ``stream_to`` streams the response to an atomic temp+rename.
        ``max_attempts`` 覆盖内层重试次数：结果上报用 1（#1098，见
        ``report``），其余传输保持默认 3。"""

        def attempt() -> tuple[int, bytes]:
            if stop is not None and stop.is_set():
                raise TransferStopped(f"{label}: stopped")
            payload = data() if callable(data) else data
            try:
                status, body = self.request(
                    method,
                    path,
                    data=payload,
                    headers=headers,
                    timeout=timeout,
                    stream_to=stream_to,
                )
            except _TRANSIENT_ERRORS as exc:
                raise _TransientTransferError(str(exc) or type(exc).__name__) from exc
            finally:
                if callable(data):
                    cast("BinaryIO", payload).close()
            if status >= 500:
                raise _TransientTransferError(f"HTTP {status}: {body[:200]!r}")
            return status, body

        try:
            result = run_with_retry(
                attempt,
                retriable=(_TransientTransferError,),
                base_seconds=_RETRY_BACKOFF_BASE_SECONDS,
                max_attempts=max_attempts,
                stop=stop,
            )
        except _TransientTransferError as exc:
            raise RuntimeError(f"{label}: {exc}") from exc
        if result is None:
            raise TransferStopped(f"{label}: stopped")
        return result

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        status, _ = self._request_with_retry(
            "GET",
            path,
            label=f"download failed: {path}",
            timeout=self.transfer_timeout,
            stream_to=destination,
        )
        if status != 200:
            raise HostRequestError(f"download failed: {path}: HTTP {status}", status)

    def upload_artifact(self, path: Path, *, stop: StopSignal | None = None) -> str:
        """Upload one output artifact, retrying transient Host failures.

        5xx responses and connection-level errors (including socket timeouts)
        get exponential backoff (1s, 2s, 4s, …); 4xx and repeated failures
        raise immediately.
        """
        status, body = self._request_with_retry(
            "POST",
            "/api/artifacts",
            data=lambda: path.open("rb"),
            label="artifact upload failed",
            timeout=self.transfer_timeout,
            stop=stop,
        )
        if status != 201:
            raise HostRequestError(f"artifact upload failed: HTTP {status}: {body[:200]!r}", status)
        return f"sha256:{json.loads(body)['hash']}"

    def release_slot(self, execution_id: str, lease_id: str) -> int:
        """Ask the Host to flip claimed -> reporting, freeing execution capacity.

        404 = Host predates this endpoint (slot held until report). No retry:
        the caller's upload queue keeps the lease alive either way.
        """
        status, _ = self.request(
            "POST",
            f"/api/agent-executions/{execution_id}/release-slot",
            headers={"X-Agent-Lease-Id": lease_id},
        )
        return status

    def report(
        self,
        execution_id: str,
        lease_id: str,
        archive: Path,
        *,
        stop: StopSignal | None = None,
    ) -> tuple[int, bytes]:
        """Submit the execution result; returns (status, body) for the caller
        to distinguish a committed report (204) from a lost lease (409).

        #843 v2：元数据在归档里（保留首成员 result.json，调用方已写好），
        请求头只带 ``X-Agent-Result-Format: 2`` + 租约头——绝不发送
        ``X-Agent-Result`` 头，v2 契约也禁止归档里的元数据携带
        ``output_artifacts_in_archive`` 标记（v1 换轨语义；Host 侧 v2 显式
        忽略，但 Worker 契约明文禁发）。

        #1098：单次尝试（``max_attempts=1``）——传输层不再内层连打。/result
        持续超时/瞬时失败时，重试全部交由 upload 报告循环（每次重试之间
        resume 心跳 → 退避 → quiesce），两次 report 尝试之间的心跳空窗
        不再叠加内层 3×timeout + 退避（修复前 ~360s > 90s 租约 TTL，租约被
        过期清扫、结果 409 丢弃）。"""
        headers: dict[str, str | bytes] = {
            RESULT_METADATA_FORMAT_HEADER: RESULT_METADATA_FORMAT_V2,
            "X-Agent-Lease-Id": lease_id,
        }
        return self._request_with_retry(
            "POST",
            f"/api/agent-executions/{execution_id}/result",
            data=lambda: archive.open("rb"),
            headers=headers,
            label=f"result report failed: {execution_id}",
            timeout=self.transfer_timeout,
            stop=stop,
            max_attempts=1,
        )

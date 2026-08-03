"""Transfer operations for the Worker's Host client: retries, downloads, uploads.

Transfer calls (bundle/artifact download, artifact/result upload) move
megabytes and share the Host with every other execution; they get a longer
timeout and exponential-backoff retry on transient failures, unlike the
control calls kept in ``worker.host_client``.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
from pathlib import Path
from typing import Any

from worker._retry import run_with_retry

# Transient network errors (timeout/reset/refused) and Host 5xx get
# exponential backoff (1s, 2s, 4s, …). Socket timeouts surface as
# TimeoutError, which is NOT a URLError subclass — catch both explicitly or
# a single 30s stall kills a finished execution.
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SECONDS = 1.0
_TRANSIENT_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    http.client.HTTPException,
)

DEFAULT_TRANSFER_TIMEOUT = 120


class HostRequestError(RuntimeError):
    """Terminal non-retryable Host response (4xx); ``status`` carries the code."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class _TransientTransferError(RuntimeError):
    """Internal carrier for one retried attempt's failure message."""


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
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, bytes]:
        raise NotImplementedError

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        label: str,
        timeout: float,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Request with backoff on transient network errors and Host 5xx.

        4xx passes through to the caller unchanged (it is a verdict, not a
        transient condition). Exhaustion raises RuntimeError carrying the
        last error with the call-site label as context.
        """

        def attempt() -> tuple[int, bytes]:
            try:
                status, body = self.request(
                    method, path, data=data, headers=headers, timeout=timeout
                )
            except _TRANSIENT_ERRORS as exc:
                raise _TransientTransferError(str(exc) or type(exc).__name__) from exc
            if status >= 500:
                raise _TransientTransferError(f"HTTP {status}: {body[:200]!r}")
            return status, body

        try:
            result = run_with_retry(
                attempt,
                retriable=(_TransientTransferError,),
                base_seconds=_RETRY_BACKOFF_BASE_SECONDS,
                max_attempts=_RETRY_MAX_ATTEMPTS,
            )
        except _TransientTransferError as exc:
            raise RuntimeError(f"{label}: {exc}") from exc
        assert result is not None  # no stop event: the loop exits via return/raise
        return result

    def download(self, path: str, destination: Path) -> None:
        status, body = self._request_with_retry(
            "GET", path, label=f"download failed: {path}", timeout=self.transfer_timeout
        )
        if status != 200:
            raise HostRequestError(f"download failed: {path}: HTTP {status}", status)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)

    def upload_artifact(self, path: Path) -> str:
        """Upload one output artifact, retrying transient Host failures.

        5xx responses and connection-level errors (including socket timeouts)
        get exponential backoff (1s, 2s, 4s, …); 4xx and repeated failures
        raise immediately.
        """
        data = path.read_bytes()
        status, body = self._request_with_retry(
            "POST",
            "/api/artifacts",
            data=data,
            label="artifact upload failed",
            timeout=self.transfer_timeout,
        )
        if status != 201:
            raise HostRequestError(f"artifact upload failed: HTTP {status}: {body[:200]!r}", status)
        return f"sha256:{json.loads(body)['hash']}"

    def release_slot(self, execution_id: str, lease_id: str) -> int:
        """Ask the Host to flip claimed -> reporting, freeing execution capacity.

        404 means the Host predates this endpoint; the caller falls back to
        holding the slot until the result report. No retry: the caller's
        upload queue keeps the lease alive either way.
        """
        status, _ = self.request(
            "POST",
            f"/api/agent-executions/{execution_id}/release-slot",
            headers={"X-Agent-Lease-Id": lease_id},
        )
        return status

    def report(
        self, execution_id: str, lease_id: str, metadata: dict[str, Any], archive: Path
    ) -> tuple[int, bytes]:
        """Submit the execution result; returns (status, body) for the caller
        to distinguish a committed report (204) from a lost lease (409)."""
        return self._request_with_retry(
            "POST",
            f"/api/agent-executions/{execution_id}/result",
            data=archive.read_bytes(),
            headers={
                "X-Agent-Result": json.dumps(metadata, ensure_ascii=True),
                "X-Agent-Lease-Id": lease_id,
            },
            label=f"result report failed: {execution_id}",
            timeout=self.transfer_timeout,
        )

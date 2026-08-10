"""Transfer operations for the Worker's Host client: retries, downloads, uploads.

Bulk transfers move megabytes and share the Host with every other execution;
they get a longer timeout and backoff retry on transient failures, unlike
the control calls in ``worker.host_client``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO, cast

import requests

from worker._retry import run_with_retry

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
        headers: dict[str, str] | None = None,
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
        headers: dict[str, str] | None = None,
        stream_to: Path | None = None,
    ) -> tuple[int, bytes]:
        """Request with backoff on transient network errors and Host 5xx.

        4xx passes through unchanged (a verdict, not a transient condition);
        exhaustion raises RuntimeError with the call-site label. A callable
        ``data`` is invoked per attempt so upload streams are re-opened on
        retry; ``stream_to`` streams the response to an atomic temp+rename.
        """

        def attempt() -> tuple[int, bytes]:
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
                max_attempts=_RETRY_MAX_ATTEMPTS,
            )
        except _TransientTransferError as exc:
            raise RuntimeError(f"{label}: {exc}") from exc
        assert result is not None  # no stop event: the loop exits via return/raise
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

    def upload_artifact(self, path: Path) -> str:
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
        self, execution_id: str, lease_id: str, metadata: dict[str, Any], archive: Path
    ) -> tuple[int, bytes]:
        """Submit the execution result; returns (status, body) for the caller
        to distinguish a committed report (204) from a lost lease (409)."""
        return self._request_with_retry(
            "POST",
            f"/api/agent-executions/{execution_id}/result",
            data=lambda: archive.open("rb"),
            headers={
                "X-Agent-Result": json.dumps(metadata, ensure_ascii=True),
                "X-Agent-Lease-Id": lease_id,
            },
            label=f"result report failed: {execution_id}",
            timeout=self.transfer_timeout,
        )

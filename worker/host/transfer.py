"""Transfer operations for the Worker's Host client: retries, downloads, uploads.

Bulk transfers move megabytes and share the Host with every other execution;
they get a longer timeout and backoff retry on transient failures, unlike
the control calls in ``worker.host.client``.
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

# #748 review P2: byte budget of the X-Agent-Result header. h11 caps one
# HTTP event (request line + ALL headers) at max_incomplete_event, default
# 16 KiB — a CJK-heavy tail at the metadata char budget serializes to ~24 KB
# even with ensure_ascii=False (6x with True) and would make the result
# UNDELIVERABLE (worse than the original bug). 14 KiB leaves headroom for
# the request line, the lease header, and proxy hop headers.
_RESULT_HEADER_BUDGET = 14 * 1024

# #748 R2 P2-1: output_artifacts are the THIRD budget face. Direct-upload
# refs (~200 bytes each, dict form) ride the SAME header on SUCCESS runs,
# and the Host-side cap is 128 (_MAX_OUTPUT_ARTIFACTS) — a full 128-ref
# manifest serializes to ~25 KB, blowing the budget exactly like the CJK
# tail did (report retry exhaustion -> lease expiry = the UNDELIVERABLE
# form this PR exists to kill). When even keeping a FRACTION of the refs
# cannot fit, the whole list degrades to empty + the truncation markers:
# the artifact BYTES are still in the result archive (and already in object
# storage for direct uploads), so the loss is "performance falls back to
# the archive channel", never data loss.


def _result_header_value(metadata: dict[str, Any]) -> bytes:
    """Serialize the result metadata into the X-Agent-Result header value.

    #748 review P2: ``ensure_ascii=False`` + UTF-8 BYTES — the escaping form
    blew every CJK char up to a 6-byte ``\\uXXXX`` sequence, and ``requests``
    refuses non-latin-1 str header values, so raw UTF-8 must go in as bytes.
    Verified roundtrip: h11 keeps header values as bytes, Starlette decodes
    latin-1, and the Host reader reverses exactly that (see
    ``_recover_result_header`` in agent_worker_results.py).

    #748 R2 P2-1: the byte budget is enforced by a THREE-STAGE degrade —
    (1) shrink ``agent_stderr_tail`` (10% steps), (2) shrink
    ``error_message`` (the classification surface, so only after the tail),
    (3) truncate ``output_artifacts`` to a prefix that fits, stamping
    ``output_artifacts_truncated: true`` + ``output_artifacts_total`` so the
    Host reader (parse_result_metadata) knows the list is a prefix. If not
    even the minimum artifact prefix fits, the list degrades to empty with
    the same markers — the artifact bytes still ride the archive.
    """
    payload = dict(metadata)

    def _serialized() -> bytes:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    while len(_serialized()) > _RESULT_HEADER_BUDGET:
        tail = payload.get("agent_stderr_tail")
        if isinstance(tail, str) and len(tail) > 200:
            payload["agent_stderr_tail"] = tail[: int(len(tail) * 0.9)]
            continue
        error = payload.get("error_message")
        if isinstance(error, str) and len(error) > 200:
            payload["error_message"] = error[: int(len(error) * 0.9)]
            continue
        artifacts = payload.get("output_artifacts")
        if isinstance(artifacts, dict) and artifacts:
            total = len(artifacts)
            payload["output_artifacts_truncated"] = True
            payload["output_artifacts_total"] = total
            # Halve the kept prefix each pass (rounding down, min 0): each
            # retry re-serializes, so the loop converges geometrically and
            # the last passes just drop the markers + empty list.
            keep = total // 2
            payload["output_artifacts"] = dict(list(artifacts.items())[:keep])
            continue
        # Nothing left to shrink (all faces minimal or absent) — ship what
        # we have; an over-budget residue can only come from exotic shapes
        # (e.g. a single ref near the budget alone), where delivery with
        # partial data still beats the UNDELIVERABLE alternative.
        break
    return _serialized()


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
        # #748: X-Agent-Result ships as raw UTF-8 BYTES (CJK-heavy metadata
        # is not latin-1-encodable as str; requests refuses the str form).
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
        # requests accepts a bytes value for a header: urllib3 writes it
        # verbatim (the latin-1 str refusal does not apply), which is how
        # the raw-UTF-8 result header gets on the wire.
        headers: dict[str, str | bytes] = {
            "X-Agent-Result": _result_header_value(metadata),
            "X-Agent-Lease-Id": lease_id,
        }
        return self._request_with_retry(
            "POST",
            f"/api/agent-executions/{execution_id}/result",
            data=lambda: archive.open("rb"),
            headers=headers,
            label=f"result report failed: {execution_id}",
            timeout=self.transfer_timeout,
        )

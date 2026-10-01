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

# #748 review P2: byte budget of the X-Agent-Result header. h11 caps one
# HTTP event (request line + ALL headers) at max_incomplete_event, default
# 16 KiB — a CJK-heavy tail at the metadata char budget serializes to ~24 KB
# even with ensure_ascii=False (6x with True) and would make the result
# UNDELIVERABLE (worse than the original bug). 14 KiB leaves headroom for
# the request line, the lease header, and proxy hop headers.
_RESULT_HEADER_BUDGET = 14 * 1024

# #748 R2 P2-1: output_artifacts are a budget face of their own.
# Direct-upload refs (~200 bytes each, dict form) ride the SAME header on
# SUCCESS runs, and the Host-side cap is 128 (_MAX_OUTPUT_ARTIFACTS) — a
# full 128-ref manifest serializes to ~25 KB, blowing the budget exactly
# like the CJK tail did (report retry exhaustion -> lease expiry = the
# UNDELIVERABLE form this PR exists to kill). #748 R3 (codex review P1): a
# kept PREFIX is NOT an acceptable degrade — in direct-upload mode
# prepare_result deliberately does NOT embed the artifact bytes in the
# archive, and the Host's completion handler does not reconstruct the
# dropped refs from the truncation markers, so every ref missing from the
# header is a missing file in job_dir and the run flips to "Missing
# outputs". When the budget forces the artifact list itself to shrink we
# therefore raise ResultHeaderOverflow instead: the upload queue catches
# it and falls back to the archive-embed channel (same as the
# direct-upload failure path), where the refs are CAS strings (~78 B
# each, 128 entries ~= 10 KB, inside the budget naturally). #755 对抗复审
# P2-1b: the command face (pure observability — with 128 outputs the argv
# repeats --require-output for ~7.7 KB) is dropped BEFORE the artifact
# list is touched, so the archive-embed fallback's CAS manifest fits
# without reaching truncation. Only a payload that STILL overflows after
# all that reaches the truncation break below — the last resort, see the
# comment there.


class ResultHeaderOverflow(RuntimeError):
    """#748 R3 (codex review P1): the result header cannot carry the full
    direct-upload artifact manifest within the byte budget.

    Raised by ``_result_header_value`` when a non-empty ``output_artifacts``
    carrying DIRECT-UPLOAD dict refs still overflows the budget. The upload
    queue catches it, clears the direct-upload spec, and re-runs prepare with
    the artifact bytes embedded in the tar (CAS string refs are ~78 B each,
    128 entries ~= 12 KB, inside the budget naturally)."""


def _has_direct_refs(artifacts: dict[str, Any]) -> bool:
    """True when the artifact manifest carries direct-upload dict refs.

    The ref form IS the transport verdict (worker/artifact/upload.py returns
    dicts for presigned PUT, the legacy channel returns "sha256:..." strings),
    so the presence of ANY dict ref means the result archive was built in
    direct mode — artifact bytes NOT embedded — and a header prefix would
    lose refs for good. String/CAS refs mean the bytes ride the archive, so
    those payloads go straight to the last-resort truncation instead."""
    return any(isinstance(ref, dict) for ref in artifacts.values())


def _result_header_value(metadata: dict[str, Any]) -> bytes:
    """Serialize the result metadata into the X-Agent-Result header value.

    #748 review P2: ``ensure_ascii=False`` + UTF-8 BYTES — the escaping form
    blew every CJK char up to a 6-byte ``\\uXXXX`` sequence, and ``requests``
    refuses non-latin-1 str header values, so raw UTF-8 must go in as bytes.
    Verified roundtrip: h11 keeps header values as bytes, Starlette decodes
    latin-1, and the Host reader reverses exactly that (see
    ``_recover_result_header`` in agent_worker_results.py).

    #748 R2 P2-1: the byte budget is enforced by a FOUR-STAGE degrade —
    (1) shrink ``agent_stderr_tail`` (10% steps), (2) shrink
    ``error_message`` (the classification surface, so only after the tail),
    (3) drop ``command`` (#755 对抗复审 P2-1b: pure observability — the
    Host records it but never judges on it; with 128 outputs the argv
    alone repeats --require-output for ~7.7 KB, so clearing it lets the
    CAS manifest fit whole), (4) the artifact list. Stage 4 #748 R3
    (codex review P1) now dispatches on the REF FORM: direct-upload dict
    refs raise ``ResultHeaderOverflow`` (fallback signal — the archive
    carries no artifact bytes, so a prefix loses refs for good; the queue
    re-prepares via the archive-embed channel, whose CAS refs are ~78 B
    each and fit the budget naturally); CAS string refs already have the
    bytes IN the archive, so they take the last-resort truncation directly
    (see that comment). This is the dead-loop guard for free: the
    fallback's rebuilt manifest is CAS-form, so a second overflow can
    never re-signal — at most one fallback per result, and the queue's
    fallback path is additionally once-only."""
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
        command = payload.get("command")
        if isinstance(command, list) and command:
            # #755 对抗复审 P2-1b：command 是纯观测面（Host 只记录、不参与
            # 完成判定），动产物清单之前先砍它——128 产物时 argv 里重复的
            # --require-output 约占 7.7KB，清空后 CAS 清单整体落预算。
            payload["command"] = []
            continue
        artifacts = payload.get("output_artifacts")
        if isinstance(artifacts, dict) and artifacts and _has_direct_refs(artifacts):
            # #748 R3 (codex review P1): do NOT truncate a direct-upload
            # manifest — the result archive was built WITHOUT the artifact
            # bytes (direct mode skips the tar embed), and the Host does
            # not reconstruct the dropped refs from the truncation markers,
            # so every ref missing from the header is a file missing from
            # job_dir and the run flips to "Missing outputs". Signal the
            # caller to switch to the archive-embed channel instead.
            raise ResultHeaderOverflow(
                "result header over budget with direct-upload output_artifacts"
                f" ({len(artifacts)} refs); archive-embed fallback required"
            )
        if isinstance(artifacts, dict) and artifacts:
            # LAST RESORT truncation, reached in exactly two shapes:
            # (a) CAS string refs — the artifact bytes are already IN the
            # archive this header ships with, so the Host unpacks them into
            # the staging view regardless of the header manifest; the
            # dropped entries are the header manifest only.
            # (b) direct refs AFTER the queue's archive-embed fallback —
            # only reachable if the fallback could not rebuild (prepare
            # failure degrade path), an already-degenerate shape.
            # Delivery with partial data beats the UNDELIVERABLE
            # alternative (report retry exhaustion -> lease expiry -> full
            # re-run).
            # MARKER SEMANTICS (#755 对抗复审 P2-1a): the markers ARE part
            # of the Host completion contract — with
            # output_artifacts_truncated set, the Host skips the
            # empty-manifest completed→failed flip and judges
            # produced/missing from the staged archive view (the bytes ride
            # the archive in shape (a)); what the markers still do NOT do
            # is reconstruct dropped DIRECT refs (shape (b) stays
            # degenerate and fails honestly via the missing check).
            payload["output_artifacts_total"] = len(artifacts)
            payload["output_artifacts_truncated"] = True
            payload["output_artifacts"] = {}
        break
    return _serialized()


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
        stop: StopSignal | None = None,
    ) -> tuple[int, bytes]:
        """Request with backoff on transient network errors and Host 5xx.

        4xx passes through unchanged (a verdict, not a transient condition);
        exhaustion raises RuntimeError with the call-site label. A callable
        ``data`` is invoked per attempt so upload streams are re-opened on
        retry; ``stream_to`` streams the response to an atomic temp+rename.
        """

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
                max_attempts=_RETRY_MAX_ATTEMPTS,
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
        metadata: dict[str, Any],
        archive: Path,
        *,
        stop: StopSignal | None = None,
        **_extra: Any,
    ) -> tuple[int, bytes]:
        """Submit the execution result; returns (status, body) for the caller
        to distinguish a committed report (204) from a lost lease (409).

        ``**_extra`` keeps older/newer queue and client shapes compatible
        across the #748 R3 fallback plumbing (the queue passes the report
        lane's keyword tail through; the serializer decides signal-vs-
        truncate from the ref FORM, not from a flag)."""
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
            stop=stop,
        )

"""#748 crash-evidence plumbing for the agent-stderr tail.

Owns the stderr-tail faces so ``prepare.py`` stays a thin caller: the
idempotency anchor (read back the scan-time sink file when a re-run of
prepare finds the events file already compressed — the direct-upload
fallback and worker-restart restore both re-enter prepare, and a second
scan of a rewritten file yields nothing) and the error_message summary
(exit code + the tail's LAST line — a crash header ends the stream).

The secret registry the redaction passes ride on lives in
``worker/upload/secret_registry.py`` (split for budget reclaim, closed
anchors #843/#844): both the tail anchor and the error_message summary
take a fresh ``secret_snapshot()`` per call — they do not cut text, so
there is no lookback margin to keep consistent (#844).
"""

from __future__ import annotations

from pathlib import Path

from shared.stderr_tail import AGENT_STDERR_FILENAME, STDERR_TAIL_BYTES
from worker.upload.secret_registry import secret_snapshot


def stderr_tail_for_run(run_dir: Path, scanned_tail: bytes) -> bytes:
    """The idempotent stderr tail for one prepare pass (#748 review P1).

    ``scanned_tail`` is the fresh scan's capture; when it is empty but the
    scan-time sink file exists (a previous pass already rescued and
    compressed the events — direct-upload fallback, worker-restart restore),
    the file IS the tail: the compression rewrite destroyed the raw lines,
    so nothing else can recover them.

    The sink is written already redacted (#748 R3), but both arms go
    through the same final redaction (#755 对抗复审 P3-3: covers anchors from
    older Workers and secrets registered after the anchor was written), and
    every cut happens after it. An anchor larger than any tail this Worker
    writes is not ours to trust and is ignored."""
    tail = scanned_tail
    if not tail:
        sink = run_dir / AGENT_STDERR_FILENAME
        if sink.is_file() and sink.stat().st_size <= STDERR_TAIL_BYTES:
            tail = sink.read_bytes()
    if not tail:
        return b""
    redacted = secret_snapshot().redact(tail.decode("utf-8", "replace"))
    return redacted.encode("utf-8")[-STDERR_TAIL_BYTES:]


def stderr_error_message(exit_code: int, stderr_tail: bytes) -> str:
    """error_message for a crashed agent process — exit code plus the
    retained stderr tail's LAST line (the crash header: a panic/trace ends
    the stream, so the newest — and most explanatory — line is the last one;
    the external API's error_summary truncates at 240 chars). The full
    multi-line tail rides the archive member + metadata; the empty tail
    keeps the legacy message unchanged.

    #748 R2 P2-2: redact FIRST, truncate AFTER. Truncating before the
    redaction left a key fragment that straddled the 200-char boundary
    un-replaced (no full-value match), leaking a secret prefix into the
    external error_message face. Redaction replaces values with ``***``
    (shorter), so the 200-char cap keeps its meaning on the redacted text.
    """
    summary = secret_snapshot().redact(stderr_tail.decode("utf-8", "replace")).strip()
    if not summary:
        return f"Agent process exited {exit_code}"
    last_line = " ".join(summary.splitlines()[-1].split())
    return f"Agent process exited {exit_code}: {last_line[:200]}"

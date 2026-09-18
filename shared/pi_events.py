"""Single-pass scan + compression for Pi events.jsonl files.

Lives in ``shared/`` because both sides run it over the raw events stream:
the Agent Worker compresses before upload, the Host compresses pi-runtime
artifacts on its own write paths. Stdlib-only by design (see
``shared/__init__.py``).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any

from shared.pi_model_error import fold_model_error

logger = logging.getLogger(__name__)

# #748: the stderr-tail budget retained by the compression pass. The bound is
# enforced as two character budgets (per-line pre-trim + running-total deque
# pop, both against this value in chars) with a final byte-slice backstop
# after the UTF-8 encode — chars↔bytes can diverge up to 4x, so the slice is
# the hard byte guarantee and the char budgets are the working bound.
# Non-JSON lines are the agent's stderr text (the spawn merges stderr into
# the stdout pipe, so both pumps write them into events.jsonl raw), and the
# compression rewrite below discards them — this tail is the only survivor,
# so it must be bounded (#637 lesson: nothing on the events path may buffer
# without a cap). Keep-the-tail, not keep-the-head: a crash stack ends the
# stream.
STDERR_TAIL_BYTES = 8 * 1024


# Event types that the job log renderer consumes.  All message_update deltas
# (thinking_delta, text_delta, toolcall_delta, ...) are discarded because the
# final state is captured in message_end events.
# ``auto_retry_start`` is the pi/velites retry-observability event; it is not
# rendered, but must survive compression so the retry history stays visible
# in the compacted events.jsonl.
# ``outputs_validation`` is the velites output self-check event (M3); not
# rendered either, but the Host needs it to judge declared-artifact state.
RELEVANT_EVENT_TYPES = frozenset(
    {
        "session",
        "agent_start",
        "agent_end",
        "turn_start",
        "turn_end",
        "message_start",
        "message_end",
        "auto_retry_start",
        "tool_execution_start",
        "tool_execution_end",
        "outputs_validation",
    }
)


def scan_and_compress_pi_events(
    events_path: Path, stderr_sink: Path | None = None
) -> tuple[str | None, int, int, bytes]:
    """One pass: fold the model-error state, capture the stderr tail, and
    rewrite the file compressed.

    Equivalent to ``detect_model_error(events_path)`` followed by
    ``compress_pi_events(events_path)``, but reads the file once instead of
    twice — the raw events stream runs to hundreds of MB per execution, so
    the second full scan dominated the upload pipeline's CPU time. The
    #748 stderr-tail capture rides the same pass for the same reason.

    ``stderr_sink`` (#748 review P1) makes the capture durable AT SCAN TIME:
    when the tail is non-empty it is persisted to the sink path BEFORE the
    rewrite replaces the events file — after the replace the non-JSON lines
    are gone forever, so a caller that re-runs this scan (upload fallback,
    worker-restart restore) must read the tail back FROM THE SINK FILE, not
    from a second scan. Best-effort: an unwritable sink is logged and never
    fails the compression (the in-memory tail still rides the return value).

    Returns ``(model_error, original_bytes, compressed_bytes, stderr_tail)``.
    ``stderr_tail`` is the bounded keep-the-tail capture of the non-JSON
    lines (the agent's merged stderr — crash traces, panic headers) that the
    compression rewrite is about to drop; ``b""`` when there are none. If
    the file cannot be processed it is left unchanged and
    ``(None, 0, 0, b"")`` is returned, matching the individual failure
    modes of the two-function equivalent.
    """
    if not events_path.is_file():
        return None, 0, 0, b""

    original_size = events_path.stat().st_size
    if original_size == 0:
        return None, 0, 0, b""

    compressed_path = events_path.with_suffix(".jsonl.compressing")
    model_error: str | None = None
    # 两重字符预算（单行预截 STDERR_TAIL_BYTES 字符 + deque 运行总量 pop 到
    # STDERR_TAIL_BYTES 字符以内），最终 encode 后再按 STDERR_TAIL_BYTES 字节
    # 切片兜底——bytes 与 chars 的比例上限是 4（UTF-8），兜底切片只在
    # 多字节字符把字符预算换算放大时收紧，不会放松上限。
    stderr_tail: deque[str] = deque()
    stderr_chars = 0
    try:
        with (
            events_path.open("r", encoding="utf-8", errors="replace") as src,
            compressed_path.open("w", encoding="utf-8") as dst,
        ):
            for raw_line in src:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event: Any = json.loads(line)
                except json.JSONDecodeError:
                    if len(line) > STDERR_TAIL_BYTES:
                        line = line[-STDERR_TAIL_BYTES:]
                    stderr_tail.append(line)
                    stderr_chars += len(line)
                    while stderr_chars > STDERR_TAIL_BYTES and len(stderr_tail) > 1:
                        stderr_chars -= len(stderr_tail.popleft())
                    continue
                if not isinstance(event, dict):
                    continue
                model_error = fold_model_error(event, model_error)
                if event.get("type") in RELEVANT_EVENT_TYPES:
                    dst.write(line + "\n")
            # 对齐 worker/_atomic 标准：replace 前 flush + fsync，崩溃不留半截文件。
            dst.flush()
            os.fsync(dst.fileno())
    except Exception:
        logger.exception("Failed to compress Pi events: %s", events_path)
        with suppress(OSError):
            compressed_path.unlink(missing_ok=True)
        return None, 0, 0, b""

    tail = "\n".join(stderr_tail).encode("utf-8", "replace")[-STDERR_TAIL_BYTES:]
    if stderr_sink is not None and tail:
        # Best-effort AT THE CALL SITE: an unwritable sink must never fail
        # the compression (the in-memory tail still rides the return value).
        # The catch lives here, not inside _persist_stderr_tail, so a
        # sink-failure in ANY form (patched, unwritable dir, os.replace
        # across devices) degrades instead of escaping into the scan.
        # shared/ is stdlib-only by design (see shared/__init__.py), so the
        # sink write carries the RAW tail — the Worker-side caller redacts
        # the file in place right after (worker/upload/stderr_evidence.py:
        # the sink IS the outbound archive face).
        try:
            _persist_stderr_tail(stderr_sink, tail)
        except OSError:
            logger.exception("Failed to persist the stderr tail: %s", stderr_sink)
    try:
        compressed_path.replace(events_path)
    except OSError:
        logger.exception("Failed to replace events file: %s", events_path)
        return None, 0, 0, b""

    compressed_size = events_path.stat().st_size
    return model_error, original_size, compressed_size, tail


def _persist_stderr_tail(sink: Path, tail: bytes) -> None:
    """Best-effort durable copy of the rescued tail (same-dir temp + replace).

    No fsync: the sink must survive process crashes (worker restart →
    restore() re-runs prepare), not power loss — page-cache write-back is
    enough for that, and the compression pass must never fail on an
    unwritable sink (the tail still rides the return value). OSError
    handling lives at the CALL SITE in scan_and_compress_pi_events, where
    any failure form (patched, unwritable dir, os.replace across devices)
    degrades to a log line instead of escaping into the scan."""
    with tempfile.NamedTemporaryFile(
        dir=sink.parent, prefix=".agent-stderr.", delete=False
    ) as staging:
        staging.write(tail)
    os.replace(staging.name, sink)


def compress_pi_events(events_path: Path) -> tuple[int, int]:
    """Rewrite a Pi events.jsonl file keeping only events needed for rendering.

    Returns ``(original_bytes, compressed_bytes)``.  If the file cannot be
    processed it is left unchanged and ``(0, 0)`` is returned.
    """
    _, original, compressed, _ = scan_and_compress_pi_events(events_path)
    return original, compressed

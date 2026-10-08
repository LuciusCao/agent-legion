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
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from shared.pi_model_error import fold_model_error
from shared.redaction import SecretSpans
from shared.stderr_tail import REDACT_WINDOW_MARGIN, StderrTail, persist_stderr_tail

logger = logging.getLogger(__name__)

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
    events_path: Path,
    stderr_sink: Path | None = None,
    secret_spans: SecretSpans | None = None,
    secret_max_chars: int = 0,
    event_observer: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str | None, int, int, bytes]:
    """One pass: fold the model-error state, capture the stderr tail, and
    rewrite the file compressed.

    Equivalent to ``detect_model_error(events_path)`` followed by
    ``compress_pi_events(events_path)``, but reads the file once instead of
    twice — the raw events stream runs to hundreds of MB per execution, so
    the second full scan dominated the upload pipeline's CPU time. The
    #748 stderr-tail capture rides the same pass for the same reason.

    Stderr capture (#748): every line that is not a JSON object is the
    agent's merged stderr — crash traces, panic headers — which the
    compression rewrite is about to drop. It is kept RAW and verbatim (line
    separators, whitespace and blank lines untouched) in a keep-the-tail
    buffer bounded by ``STDERR_TAIL_BYTES + margin`` characters, ``margin =
    max(REDACT_WINDOW_MARGIN, secret_max_chars)``; nothing is redacted or
    normalized while streaming.

    Redaction (#755) runs exactly once, on that buffer at the end, and the
    output is taken from it with one cut (``shared/stderr_tail.py``):

    * The first ``margin`` characters of a trimmed buffer are lookback
      only — never emitted. A secret cut by the trim lost its head there;
      at ≤ ``margin`` characters its surviving fragment lies wholly inside
      the lookback.
    * Every secret that reaches the emitted window is therefore complete
      in the buffer, so ``secret_spans`` sees it whole; the window start is
      widened back to the start of any span straddling it, and spans are
      replaced before the text is encoded and byte-cut.
    * Hence ``secret_max_chars`` must be ≥ the longest literal the caller
      can match. Shape rules (``sk-…``, JWT) have no length bound; a shape
      token longer than the lookback can be cut by the trim, so after any
      cut the leading partial line (or, on a single line, partial token)
      is dropped too.

    ``secret_spans`` raising is fail-closed: the tail is dropped (``b""``)
    and nothing reaches the sink. ``None`` keeps everything raw (tests and
    Host callers with no secret registry). Matching domain: the file is
    decoded as UTF-8 with ``errors="replace"`` and universal newlines, so
    secrets are matched in that decoded form (``\\n`` line endings, U+FFFD
    for undecodable bytes) — the Worker-side registry
    (worker/upload/stderr_evidence.py) registers its values in that domain.

    ``stderr_sink`` (#748 review P1) makes the capture durable AT SCAN TIME:
    a non-empty (already redacted) tail is persisted to the sink path BEFORE
    the rewrite replaces the events file — after the replace the non-JSON
    lines are gone forever, so a caller that re-runs this scan (upload
    fallback, worker-restart restore) must read the tail back FROM THE SINK
    FILE. Best-effort: an unwritable sink is logged and never fails the
    compression (the in-memory tail still rides the return value).

    ``event_observer`` (#952) sees every parsed JSON-object event in the same
    pass (e.g. ``OutputTruncation.observe`` counting ``stopReason=length``),
    so extra per-event facts never cost a second full scan.

    Returns ``(model_error, original_bytes, compressed_bytes, stderr_tail)``;
    ``stderr_tail`` is ``b""`` when there is none. If the file cannot be
    processed it is left unchanged and ``(None, 0, 0, b"")`` is returned,
    matching the individual failure modes of the two-function equivalent.
    """
    if not events_path.is_file() or (original_size := events_path.stat().st_size) == 0:
        return None, 0, 0, b""

    compressed_path = events_path.with_suffix(".jsonl.compressing")
    model_error: str | None = None
    stderr = StderrTail(max(REDACT_WINDOW_MARGIN, secret_max_chars))
    try:
        with (
            events_path.open("r", encoding="utf-8", errors="replace") as src,
            compressed_path.open("w", encoding="utf-8") as dst,
        ):
            for raw_line in src:
                line = raw_line.strip()
                try:
                    event: Any = json.loads(line)
                except json.JSONDecodeError:
                    event = None
                if not isinstance(event, dict):
                    stderr.append(raw_line)
                    continue
                model_error = fold_model_error(event, model_error)
                if event_observer is not None:
                    event_observer(event)
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

    tail = stderr.finish(secret_spans)
    if stderr_sink is not None and tail:
        # Best-effort AT THE CALL SITE: an unwritable sink must never fail
        # the compression (the in-memory tail still rides the return value).
        try:
            persist_stderr_tail(stderr_sink, tail)
        except Exception:
            # #204 broad-except audit: sink 落盘是纯观测面，压缩/返回值才是
            # 关键路径——失败语义是「本次不留锚点」（重入路径归因降级为空，
            # 内存 tail 仍随返回值走），任何失败族都必须降级而非把 run 改判
            # failed。结果空间：锚点缺失是唯一后果，恢复路径对此有定义
            # （tail 读回为空）。日志保全：logger.exception 带堆栈。
            logger.exception("Failed to persist the stderr tail: %s", stderr_sink)
    try:
        compressed_path.replace(events_path)
    except OSError:
        logger.exception("Failed to replace events file: %s", events_path)
        return None, 0, 0, b""

    compressed_size = events_path.stat().st_size
    return model_error, original_size, compressed_size, tail


def compress_pi_events(events_path: Path) -> tuple[int, int]:
    """Rewrite a Pi events.jsonl file keeping only events needed for rendering.

    Returns ``(original_bytes, compressed_bytes)``.  If the file cannot be
    processed it is left unchanged and ``(0, 0)`` is returned.
    """
    _, original, compressed, _ = scan_and_compress_pi_events(events_path)
    return original, compressed

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
from typing import Any, TextIO

from shared.pi_model_error import fold_model_error
from shared.redaction import SecretRedactor
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


# #842 评审 P3-2：model_error 归因串脱敏失败时的降级占位——非空（保住
# failed 归因，状态不翻转为 completed），固定文本不携带任何原文。
MODEL_ERROR_REDACTION_FAILED = "model error (redaction failed)"


def _dump_event(event: dict[str, Any]) -> str:
    """Compact one-line serialization of a kept (redacted) event (#842) —
    the real Pi stream is compact and the renderer accepts either form."""
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def _write_kept_event(dst: TextIO, event: Any, line: str, redactor: SecretRedactor | None) -> None:
    """Write one kept event line, its string values redacted (#842).

    ANY per-line failure — the span function escaping, the re-serialization,
    or the write itself — drops the whole line fail-closed. A raw line must
    never be written, and a per-line failure must never escalate to a
    whole-scan failure: the scan-wide path leaves the events file
    UNCOMPRESSED and UNREDACTED for the result archive, and the failure
    family only redaction can trigger (a hit line with JSON-escaped lone
    surrogates — ``json.loads`` yields real surrogate characters that
    ``ensure_ascii=False`` cannot UTF-8-encode on write) correlates exactly
    with a secret hit. ``redactor is None`` (Host path) and an unchanged
    event keep the ORIGINAL line bytes, so clean streams are byte-identical
    with and without the switch and size accounting never drifts."""
    try:
        redacted = redactor.redact_json(event) if redactor is not None else event
        out = line if redacted == event else _dump_event(redacted)
        dst.write(out + "\n")
    except Exception:
        # #204 broad-except audit: 单事件行的脱敏/重序列化/写出任一逃逸一律
        # 整行丢弃 fail-closed（机制见 docstring）：绝不把 raw 行写进压缩
        # 文件，也绝不升级成整趟扫描失败（整趟失败会让未压缩未脱敏的
        # events.jsonl 原样留给归档外发）。结果空间：渲染日志缺该事件（纯
        # 观测面降级），压缩与其余事件照常。日志保全：exception 带堆栈。
        logger.exception("Failed to write the redacted event line; dropping it")


def scan_and_compress_pi_events(
    events_path: Path,
    stderr_sink: Path | None = None,
    redactor: SecretRedactor | None = None,
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
    max(REDACT_WINDOW_MARGIN, redactor.max_chars)``; nothing is redacted or
    normalized while streaming.

    Redaction (#755) runs exactly once, on that buffer at the end, and the
    output is taken from it with one cut (``shared/stderr_tail.py``):

    * The first ``margin`` characters of a trimmed buffer are lookback
      only — never emitted. A secret cut by the trim lost its head there;
      at ≤ ``margin`` characters its surviving fragment lies wholly inside
      the lookback.
    * Every secret that reaches the emitted window is therefore complete
      in the buffer, so the span function sees it whole; the window start is
      widened back to the start of any span straddling it, and spans are
      replaced before the text is encoded and byte-cut.
    * Hence ``redactor.max_chars`` must be ≥ the longest literal the
      snapshot's span function can match — guaranteed by the snapshot being
      ONE immutable registry read (#844; the two separate reads it replaced
      could race a registration between them). Shape rules (``sk-…``, JWT)
      have no length bound; a shape token longer than the lookback can be
      cut by the trim, so after any cut the leading partial line (or, on a
      single line, partial token) is dropped too.

    Events redaction (#842): with a ``redactor`` snapshot, the STRING
    VALUES of kept events are redacted in the same pass — a
    ``tool_execution_end`` whose tool output echoes a registered secret
    (``bash env``, ``cat`` of a config file) would otherwise ride the
    compressed file to the Host and the job-log renderer verbatim. Only
    string values are rewritten (``SecretRedactor.redact_json``), so the
    output line is re-serialized valid JSON with the same structure; the
    returned ``model_error`` attribution string is redacted too (it flows
    into result metadata / error_message). A span function that RAISES on
    an event fails closed: that line is dropped from the output — never
    written raw. The SAME drop covers a hit line whose re-serialization
    cannot be written (JSON-escaped lone surrogates decode to real
    surrogate characters that ``ensure_ascii=False`` cannot UTF-8-encode):
    dropping the line beats failing the scan, which would leave the raw
    file for the result archive. A redaction failure on the
    ``model_error`` string degrades the attribution to
    ``MODEL_ERROR_REDACTION_FAILED`` instead of failing the scan.
    ``None`` keeps the Host write path byte-verbatim (no registry on that
    side, no per-line JSON cost).

    ``redactor.spans`` raising on the stderr tail is fail-closed: the tail
    is dropped (``b""``) and nothing reaches the sink. ``None`` keeps
    everything raw (tests and Host callers with no secret registry).
    Matching domain: the file is decoded as UTF-8 with
    ``errors="replace"`` and universal newlines, so secrets are matched in
    that decoded form (``\\n`` line endings, U+FFFD for undecodable bytes) —
    the Worker-side registry (worker/upload/stderr_evidence.py) registers
    its values in that domain.

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
    processed it is left unchanged, the ``.jsonl.compressing`` staging file
    is removed on every failure path, and ``(None, 0, 0, b"")`` is returned,
    matching the individual failure modes of the two-function equivalent.
    """
    if not events_path.is_file() or (original_size := events_path.stat().st_size) == 0:
        return None, 0, 0, b""

    compressed_path = events_path.with_suffix(".jsonl.compressing")
    model_error: str | None = None
    stderr = StderrTail(max(REDACT_WINDOW_MARGIN, redactor.max_chars if redactor else 0))
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
                    _write_kept_event(dst, event, line, redactor)
            # 对齐 worker/_atomic 标准：replace 前 flush + fsync，崩溃不留半截文件。
            dst.flush()
            os.fsync(dst.fileno())
        # #842：model_error 归因串流进 result metadata / error_message（外部
        # error_summary 面），与压缩事件同一快照脱敏——provider 报错回显密钥
        # （"invalid key sk-…"）不再外发。评审 P3-2：脱敏逃逸不得炸穿扫描
        # （炸穿留下 .compressing 残留、把 run 改判 failed 丢结果）——降级为
        # 固定占位：保住失败归因（状态不翻转为 completed），不携带任何原文。
        try:
            model_error = redactor.redact(model_error) if model_error and redactor else model_error
        except Exception:
            # #204 broad-except audit: 归因串脱敏失败是纯观测面降级点——回退
            # 固定占位（不外发原文、不丢 failed 归因），扫描/压缩/尾部照常
            # 完成。结果空间：error_message 显示占位文案。日志保全：exception
            # 带堆栈。
            logger.exception("Model-error redaction failed; degrading the attribution")
            model_error = MODEL_ERROR_REDACTION_FAILED
        tail = stderr.finish(redactor.spans if redactor is not None else None)
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
        # 评审 P3-2：replace 纳入统一异常路径——旧 replace 臂直接 return，
        # .compressing 残留会随 run 目录进归档；现在一切内部失败都在同一
        # 个 except 里清理 staging 后按整趟失败返回。
        compressed_path.replace(events_path)
    except Exception:
        logger.exception("Failed to compress Pi events: %s", events_path)
        with suppress(OSError):
            compressed_path.unlink(missing_ok=True)
        return None, 0, 0, b""

    return model_error, original_size, events_path.stat().st_size, tail


def compress_pi_events(events_path: Path) -> tuple[int, int]:
    """Rewrite a Pi events.jsonl file keeping only events needed for rendering.

    Returns ``(original_bytes, compressed_bytes)``.  If the file cannot be
    processed it is left unchanged and ``(0, 0)`` is returned.
    """
    _, original, compressed, _ = scan_and_compress_pi_events(events_path)
    return original, compressed

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
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from shared.pi_model_error import fold_model_error

logger = logging.getLogger(__name__)

# #748: the stderr-tail budget retained by the compression pass. The bound is
# enforced as a per-line pre-trim (via _redact_then_tail_text) + a running-total
# deque pop (chars) with a final byte-slice backstop after the UTF-8 encode —
# chars↔bytes can diverge up to 4x, so the slice is the hard byte guarantee
# and the working budgets keep the buffered text near it.
# Non-JSON lines are the agent's stderr text (the spawn merges stderr into
# the stdout pipe, so both pumps write them into events.jsonl raw), and the
# compression rewrite below discards them — this tail is the only survivor,
# so it must be bounded (#637 lesson: nothing on the events path may buffer
# without a cap). Keep-the-tail, not keep-the-head: a crash stack ends the
# stream.
STDERR_TAIL_BYTES = 8 * 1024

# #755 对抗复审 P3-2 + codex review P1：脱敏窗口比最终保尾界宽出这一段——
# 切割先于脱敏时，骑跨切割点的密钥只剩尾段（整值匹配不上，明文外泄）；
# 扩窗让跨点密钥在脱敏时保持完整，脱敏后再切回 8KB（与
# stderr_error_message 200 字符面「先脱敏后截」同纪律）。字符面（单行
# 预截）由 _redact_then_tail_text 收口，字节面（sink 扩窗）在下方调用点
# 保持同序——先后顺序不再散落各出口。窗口外沿仍可能骑跨更长密钥——
# 扩窗压低概率而非根除，这是 best-effort 边界。
_REDACT_WINDOW_MARGIN = 512


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
    redact: Callable[[bytes], bytes] | None = None,
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

    ``redact`` (#748 R3, codex review P1) is applied BEFORE any durable
    write: the sink file never holds the raw tail, so a Worker exiting
    between this pass and a later rewrite cannot leave plaintext secrets
    in ``agent-stderr.log``. The callback is injected by the caller
    (shared/ is stdlib-only and must not import worker modules); ``None``
    keeps everything raw (tests / non-secret callers). #755 codex review
    P1: the per-line pre-trim of stderr text funnels through
    ``_redact_then_tail_text`` (widen past the budget → redact → cut), so a
    secret straddling a cut point is matched whole instead of leaking its
    tail fragment. One consequence: the RETURN value stays RAW for lines
    inside the budget — the caller-facing faces (error_message etc.)
    redact with their own, richer context
    (worker/upload/stderr_evidence.py) — but a single line that had to be
    pre-truncated returns REDACTED: the safer direction, since the
    fragment surviving a raw cut is unmatchable downstream. #755 对抗复审
    P3-2: the return face's final byte cut is line-aligned (a real cut
    drops the leading partial line).

    Returns ``(model_error, original_bytes, compressed_bytes, stderr_tail)``.
    ``stderr_tail`` is the bounded keep-the-tail capture of the non-JSON
    lines (the agent's merged stderr — crash traces, panic headers) that the
    compression rewrite is about to drop; ``b""`` when there are none. If
    the file cannot be processed it is left unchanged and
    ``(None, 0, 0, b"")`` is returned, matching the individual failure
    modes of the two-function equivalent.
    """
    if not events_path.is_file() or (original_size := events_path.stat().st_size) == 0:
        return None, 0, 0, b""

    compressed_path = events_path.with_suffix(".jsonl.compressing")
    model_error: str | None = None
    # 单行预截走 _redact_then_tail_text 漏斗（扩窗 → 脱敏 → 再截），deque 运行
    # 总量按字符 pop 整行（无骑跨），最终 encode 后再按 STDERR_TAIL_BYTES
    # 字节切片兜底——bytes 与 chars 的比例上限是 4（UTF-8），兜底切片只在
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
                    line = _redact_then_tail_text(line, redact)
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

    encoded_tail = "\n".join(stderr_tail).encode("utf-8", "replace")
    tail = _keep_tail_slice(encoded_tail)
    if stderr_sink is not None and tail:
        # Best-effort AT THE CALL SITE: an unwritable sink must never fail
        # the compression (the in-memory tail still rides the return value).
        # The catch lives here, not inside _persist_stderr_tail, so a
        # sink-failure in ANY form (patched, unwritable dir, os.replace
        # across devices) degrades instead of escaping into the scan.
        # #748 R3 (codex review P1): redaction happens BEFORE the durable
        # write — the sink file must never hold the raw tail (the return
        # value stays raw; the caller redacts its own faces separately).
        # #755 对抗复审 P3-2: redact a window wider than the final cut
        # (_REDACT_WINDOW_MARGIN) so a secret straddling the 8KB cut
        # point is still matched whole, then slice AFTER redaction.
        try:
            window = encoded_tail[-(STDERR_TAIL_BYTES + _REDACT_WINDOW_MARGIN) :]
            persisted = redact(window) if redact is not None else window
            _persist_stderr_tail(stderr_sink, _keep_tail_slice(persisted))
        except Exception:
            # #204 broad-except audit: sink 落盘是纯观测面，压缩/返回值才是
            # 关键路径——失败语义是「本次不留锚点」（重入路径归因降级为空，
            # 内存 tail 仍随返回值走），任何失败族（OSError 写失败、redact
            # 回调的非 OSError 逃逸——#755 对抗复审 P3-4）都必须降级而非把
            # run 改判 failed。结果空间：锚点缺失是唯一后果，恢复路径对此
            # 有定义（tail 读回为空）。日志保全：logger.exception 带堆栈。
            logger.exception("Failed to persist the stderr tail: %s", stderr_sink)
    try:
        compressed_path.replace(events_path)
    except OSError:
        logger.exception("Failed to replace events file: %s", events_path)
        return None, 0, 0, b""

    compressed_size = events_path.stat().st_size
    return model_error, original_size, compressed_size, tail


def _redact_then_tail_text(text: str, redact: Callable[[bytes], bytes] | None) -> str:
    """The ONE redact-then-truncate funnel for CHARACTER-face cuts of the
    stderr tail (#755 codex review P1): when ``text`` exceeds the budget,
    widen the window by ``_REDACT_WINDOW_MARGIN``, redact, THEN cut to
    ``STDERR_TAIL_BYTES``.

    Cut-before-redact leaks: a secret straddling the cut point loses its
    head with the dropped part, and whole-value matchers cannot match the
    surviving tail fragment — plaintext rides every downstream face
    (return value → error_message/metadata, sink anchor → archive). The
    widened window lets a straddling secret match whole; cutting AFTER
    redaction keeps the budget meaningful (redaction only ever shrinks).
    This is the same discipline as the sink's widened byte window below
    and stderr_error_message's redact-then-200-chars; the deque
    running-total pop drops whole lines (no straddle possible) and the
    RAW return face's final cut is line-aligned (``_keep_tail_slice``) —
    neither needs this funnel.

    In-budget text is returned untouched (the return face stays RAW by
    contract; the caller redacts with richer context). ``redact=None``
    degrades to pure truncation (legacy behavior for non-secret callers).
    A redact callback failure degrades to the raw cut — redaction is
    best-effort by contract (worker/upload/stderr_evidence.py re-redacts
    the return face) and must not kill the compression pass (the same
    discipline as the sink call site's broad catch)."""
    window = text[-(STDERR_TAIL_BYTES + _REDACT_WINDOW_MARGIN) :]
    if redact is not None and len(text) > STDERR_TAIL_BYTES:
        try:
            window = redact(window.encode("utf-8", "replace")).decode("utf-8", "replace")
        except Exception:
            logger.exception("Redact callback failed; keeping the raw tail cut")
    return window[-STDERR_TAIL_BYTES:]


def _keep_tail_slice(data: bytes) -> bytes:
    """Slice to the last STDERR_TAIL_BYTES, dropping the leading partial line
    when a real cut happened.

    #755 对抗复审 P3-2: a mid-line cut can leave the TAIL FRAGMENT of a
    secret that straddles the boundary — the redaction passes match whole
    values only, so the fragment would survive onto the RAW return face
    (→ error_message/metadata). Line-aligning the cut removes the
    straddling fragment structurally; a single line longer than the whole
    budget keeps the raw cut (the widened redact window in
    ``_redact_then_tail`` is the backstop there)."""
    if len(data) <= STDERR_TAIL_BYTES:
        return data
    cut = data[-STDERR_TAIL_BYTES:]
    if data[-STDERR_TAIL_BYTES - 1 :][:1] == b"\n":
        return cut  # 切点恰好落在行首，无残行
    newline = cut.find(b"\n")
    return cut[newline + 1 :] if newline != -1 else cut


def _persist_stderr_tail(sink: Path, tail: bytes) -> None:
    """Best-effort durable copy of the REDACTED tail (same-dir temp + replace).

    No fsync: the sink must survive process crashes (worker restart →
    restore() re-runs prepare), not power loss — page-cache write-back is
    enough for that, and the compression pass must never fail on an
    unwritable sink (the tail still rides the return value). OSError
    handling lives at the CALL SITE in scan_and_compress_pi_events, where
    any failure form (patched, unwritable dir, os.replace across devices)
    degrades to a log line instead of escaping into the scan.

    #748 R3 (codex review P1): the caller passes already-redacted bytes, and
    a failed replace must not leave the staging file behind either — the
    temp file holds the same (redacted) content, but a leaked
    ``.agent-stderr.*`` staging file in the run dir ships in the result
    archive. Cleanup is best-effort (unlink of an already-gone file is
    suppressed), and never masks the original replace failure."""
    with tempfile.NamedTemporaryFile(
        dir=sink.parent, prefix=".agent-stderr.", delete=False
    ) as staging:
        staging.write(tail)
    try:
        os.replace(staging.name, sink)
    except BaseException:
        with suppress(OSError):
            os.unlink(staging.name)
        raise


def compress_pi_events(events_path: Path) -> tuple[int, int]:
    """Rewrite a Pi events.jsonl file keeping only events needed for rendering.

    Returns ``(original_bytes, compressed_bytes)``.  If the file cannot be
    processed it is left unchanged and ``(0, 0)`` is returned.
    """
    _, original, compressed, _ = scan_and_compress_pi_events(events_path)
    return original, compressed

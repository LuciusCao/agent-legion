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
# 保持同序——先后顺序不再散落各出口。#755 codex P1：固定窗口装不下
# >512 字节的已注册密钥（PEM、长 JWT），有效窗口由调用方按已注册最长
# 密钥的字节数扩窗（max(本常量, redact_secret_max_bytes)，Worker 侧来源
# 是 worker/upload/stderr_evidence.max_secret_bytes）；残留边界收窄为
# 「未注册的他机密钥」——形态规则兜底的已知 best-effort 面。
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
    redact_secret_max_bytes: int = 0,
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
    tail fragment. #755 对抗复审 P1-1: the retained buffer is redacted WHOLE
    before the final cut (``_redact_tail_buffer``), so multi-line secrets
    (PEM) are matched across line boundaries and BOTH faces (return value
    and sink anchor) derive from the same redacted buffer — the caller's
    re-redaction (worker/upload/stderr_evidence.py) degrades to a pure
    defense net. #755 对抗复审
    P3-2: the return face's final byte cut is line-aligned (a real cut
    drops the leading partial line). #755 终审 P2-1: the funnel gate is
    byte-measured (a CJK single line can be ≤ the budget in chars yet far
    over it in bytes — the char gate used to let it slip through unredacted
    into the raw byte cut), and the final cut's no-newline arm (one line
    spanning the whole window, no line boundary to align to) redacts the
    whole buffer before slicing instead of raw-cutting an unredacted face.
    #755 codex R8 P1: the deque retains each non-JSON line in RAW form
    (line separators and leading/trailing whitespace untouched, blank
    lines kept) and the whole-buffer redaction runs on the ``""``-joined
    verbatim text — a registered multi-line secret ending in a newline
    (typical PEM) that terminates the stream now matches whole, where the
    old strip + ``"\n".join`` normalization had already eaten the trailing
    newline before any redaction could run. Display normalization
    (``_display_form``: one trailing newline dropped) happens only AFTER
    redaction. Matching domain caveat: the file is decoded as UTF-8 with
    ``errors="replace"`` and universal newlines, so secrets are matchable
    only as their UTF-8 text form with ``\n`` line endings — arbitrary
    non-UTF-8 byte secrets can never whole-match (the ``\r\n``/``\r``
    forms in the stream reach the matcher already translated to ``\n``).

    ``redact_secret_max_bytes`` (#755 codex P1) widens the redaction window
    past ``_REDACT_WINDOW_MARGIN`` to the caller's longest registered
    secret — a >512-byte key (PEM, long JWT) straddling the cut otherwise
    still loses its head before the whole-value matcher can run. The
    effective margin is ``max(_REDACT_WINDOW_MARGIN,
    redact_secret_max_bytes)`` and feeds the per-line funnel AND the deque
    retention budget alike (#755 对抗复审 P1-1: a narrow retention budget
    would popleft a multi-line secret's head lines before the whole-buffer
    redaction could ever see them).

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
    # 单行预截走 _redact_then_tail_text 漏斗（字节口径闸门 → 扩窗 → 脱敏 →
    # 再截），deque 运行总量按字符 pop 整行（无骑跨），最终 encode 后再按
    # STDERR_TAIL_BYTES 字节切片兜底——bytes 与 chars 的比例上限是 4
    # （UTF-8），兜底切片只在多字节字符把字符预算换算放大时收紧，不会放松
    # 上限。#755 终审 P2-1：兜底切片经 redact 回调过一道（无换行的单行
    # 超预算形态无行界可对齐，先对整体脱敏再保尾）。
    # #755 codex P1：有效扩窗 margin 对齐调用方已注册的最长密钥。
    # #755 对抗复审 P1-1：deque 保留预算必须同步放宽 margin——多行密钥
    # （PEM）的每行是 deque 的独立条目，若保留界仍是裸 8192 字符，密钥的
    # 头部整行会在任何扩窗脱敏运行之前被 popleft 丢弃，整值匹配永远失配
    # （残段直落锚点）。放宽后：保留界（8192+margin 字符）≥（8192+margin）
    # 字节 ≥ 最终切割点 + 最长密钥字节数，任何骑跨切割点的已注册密钥都
    # 完整落在保留缓冲内，下方对整段缓冲的一次脱敏必然整值命中。
    redact_margin = max(_REDACT_WINDOW_MARGIN, redact_secret_max_bytes)
    stderr_tail: deque[str] = deque()
    stderr_chars = 0
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
                    # #755 codex R8 P1：进保留缓冲的必须是该行的原始形态
                    # （行尾换行与前导/尾随空白原样保留，白行也不丢）——
                    # strip + 换行重组的展示归一化发生在脱敏之前时，末行
                    # 尾换行被吃掉，带尾换行的已注册多行密钥（典型 PEM）
                    # 整值匹配必然失配、明文落全部出口面；归一化只允许
                    # 在脱敏之后的展示面发生（_display_form）。
                    kept = _redact_then_tail_text(raw_line, redact, redact_margin)
                    stderr_tail.append(kept)
                    stderr_chars += len(kept)
                    while stderr_chars > STDERR_TAIL_BYTES + redact_margin and len(stderr_tail) > 1:
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

    # 原始形态缓冲：每行保留自身分隔符，空串 join 逐字复原（带尾换行的
    # 密钥整值因此可被命中）；展示归一化（去末尾一个换行，保持既有出口
    # 契约）在脱敏之后、最终切割之前由 _display_form 完成。
    encoded_tail = "".join(stderr_tail).encode("utf-8", "replace")
    # #755 对抗复审 P1-1：脱敏跑在「完整保留缓冲」上、任何最终切割之前——
    # 多行密钥（PEM）只有 join 后的整体形态能被整值替换命中（逐行脱敏
    # 结构性接不住换行密钥）。deque 保留界已按 redact_margin 放宽（见上），
    # 任何骑跨最终 8KB 切割点的已注册密钥都完整落在这段缓冲里。sink 与
    # return 两面从同一份脱敏后缓冲派生，不再各自开窗口。
    redacted_tail = _redact_tail_buffer(encoded_tail, redact)
    if redacted_tail is None:
        # 脱敏器逃逸：raw 缓冲绝不落盘（锚点直接放弃），return 面退回
        # raw 契约（调用方出口面经 stderr_evidence 重脱敏）。
        tail = _keep_tail_slice(_display_form(encoded_tail))
    else:
        tail = _keep_tail_slice(_display_form(redacted_tail), redact)
    if stderr_sink is not None and tail and redacted_tail is not None:
        # Best-effort AT THE CALL SITE: an unwritable sink must never fail
        # the compression (the in-memory tail still rides the return value).
        # The catch lives here, not inside _persist_stderr_tail, so a
        # sink-failure in ANY form (patched, unwritable dir, os.replace
        # across devices) degrades instead of escaping into the scan.
        # #748 R3 (codex review P1): redaction happens BEFORE the durable
        # write — the sink file never holds the raw tail.
        # #755 对抗复审 P1-1 起 return 面也走同一份脱敏后缓冲（不再保留
        # raw 面），调用方的二次重脱敏（stderr_evidence）随之退化为纯防御网。
        try:
            _persist_stderr_tail(stderr_sink, tail)
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


def _redact_tail_buffer(data: bytes, redact: Callable[[bytes], bytes] | None) -> bytes | None:
    """#755 对抗复审 P1-1：在任何最终切割之前对「完整保留缓冲」整体脱敏。

    逐行脱敏结构性接不住换行密钥（PEM 的值跨行，任何单行 replace 都
    匹配不到整值）；deque 保留界已按 redact_margin 放宽（骑跨最终切割
    点的已注册密钥完整落在缓冲内），所以这里的一次整段脱敏必然整值
    命中。``redact=None``（旧式/无密钥调用方）原样返回；回调逃逸返回
    ``None``——调用方据此放弃锚点落盘（脱敏器都炸了，raw 缓冲绝不能
    写 durable 面），return 面退回 raw（调用方出口面经
    stderr_evidence 重脱敏，与旧契约同）。"""
    if redact is None or not data:
        return data
    try:
        return redact(data)
    except Exception:
        logger.exception("Redact callback failed on the retained tail buffer; sink write skipped")
        return None


def _redact_then_tail_text(
    text: str, redact: Callable[[bytes], bytes] | None, margin: int = _REDACT_WINDOW_MARGIN
) -> str:
    """The ONE redact-then-truncate funnel for CHARACTER-face cuts of the
    stderr tail (#755 codex review P1): when ``text`` exceeds the budget,
    widen the window by ``margin``, redact, THEN cut to
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
    RAW return face's final cut is line-aligned (``_keep_tail_slice``).

    #755 终审 P2-1: the gate is measured in BYTES, not chars — the budget
    is a byte budget, and a single CJK line (chars ≤ 8192 but bytes up to
    3x over) used to slip past the char gate unredacted into the final
    byte cut, whose no-newline arm then raw-sliced the straddling secret's
    tail fragment onto the return face. The char window itself needs no
    byte conversion: ``STDERR_TAIL_BYTES + margin`` chars are always ≥ the
    same number of bytes, so the redacted window still covers the final
    byte cut with the whole margin.

    In-budget text is returned untouched HERE (the buffer-level whole
    redaction in ``_redact_tail_buffer`` owns the outbound faces now —
    #755 对抗复审 P1-1); ``redact=None``
    degrades to pure truncation (legacy behavior for non-secret callers).
    A redact callback failure degrades to the raw cut — redaction is
    best-effort by contract (worker/upload/stderr_evidence.py re-redacts
    the return face) and must not kill the compression pass (the same
    discipline as the sink call site's broad catch)."""
    window = text[-(STDERR_TAIL_BYTES + margin) :]
    if redact is not None and len(text.encode("utf-8", "replace")) > STDERR_TAIL_BYTES:
        try:
            window = redact(window.encode("utf-8", "replace")).decode("utf-8", "replace")
        except Exception:
            logger.exception("Redact callback failed; keeping the raw tail cut")
    return window[-STDERR_TAIL_BYTES:]


def _display_form(data: bytes) -> bytes:
    """展示面归一化（#755 codex R8 P1）：去掉末尾一个换行，保持「tail 不以
    换行收尾」的既有出口契约（sink 锚点与 return 面同形）。

    只允许在脱敏之后调用：脱敏需要原始形态缓冲（带尾换行的已注册密钥靠
    末尾换行整值命中，先归一化即 P1 的失配根因），而移除末尾换行字节
    永远不可能让密钥字节显形——方向安全。"""
    return data[:-1] if data.endswith(b"\n") else data


def _keep_tail_slice(data: bytes, redact: Callable[[bytes], bytes] | None = None) -> bytes:
    """Slice to the last STDERR_TAIL_BYTES, dropping the leading partial line
    when a real cut happened.

    #755 对抗复审 P3-2: a mid-line cut can leave the TAIL FRAGMENT of a
    secret that straddles the boundary — the redaction passes match whole
    values only, so the fragment would survive onto the RAW return face
    (→ error_message/metadata). Line-aligning the cut removes the
    straddling fragment structurally.

    #755 终审 P2-1: the no-newline arm (a single line covering the whole
    cut window) has no line boundary to align to, so it must NOT raw-slice
    an unredacted face — the line may have slipped the per-line funnel
    only via the pre-fix char gate, and belt-and-suspenders costs one
    bounded pass. Redact the WHOLE buffer first (the straddling secret is
    complete inside it — its head lies before the cut point), then take
    the tail; on the happy path the line was already redacted by the
    byte-gated funnel and this pass is a no-op. ``redact=None`` (legacy /
    non-secret callers) keeps the raw cut; a callback failure degrades to
    the raw cut, same best-effort discipline as the funnel."""
    if len(data) <= STDERR_TAIL_BYTES:
        return data
    cut = data[-STDERR_TAIL_BYTES:]
    if data[-STDERR_TAIL_BYTES - 1 :][:1] == b"\n":
        return cut  # 切点恰好落在行首，无残行
    newline = cut.find(b"\n")
    if newline != -1:
        return cut[newline + 1 :]
    if redact is not None:
        try:
            return redact(data)[-STDERR_TAIL_BYTES:]
        except Exception:
            # #204 broad-except audit: 二次防御脱敏（主脱敏在单行漏斗已完成）
            # 的回调逃逸不得击穿压缩通道——退化方向是保持行内 raw 切（修复前
            # 行为），调用方出口面（stderr_evidence）仍会统一重脱敏。结果
            # 空间：残段可能进 return face，与 funnel 内同款捕获同纪律。
            # 日志保全：logger.exception 带堆栈。
            logger.exception("Redact callback failed; keeping the raw tail cut")
    return cut


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

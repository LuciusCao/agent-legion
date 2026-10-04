"""Bounded, redact-once stderr tail for the Pi events scan (#748/#755).

The capture contract (raw buffer, lookback never emitted, one redaction
pass, cuts only after it) is documented on
``shared.pi_events.scan_and_compress_pi_events``, its only caller.
Stdlib-only (see ``shared/__init__.py``).
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from shared.redaction import SecretSpans, Span, apply_spans, merge_spans

logger = logging.getLogger(__name__)

# #748: the stderr-tail budget retained by the compression pass. Non-JSON
# lines are the agent's stderr text (the spawn merges stderr into the stdout
# pipe, so both pumps write them into events.jsonl raw), and the compression
# rewrite discards them — this tail is the only survivor, so it must be
# bounded (#637 lesson: nothing on the events path may buffer without a cap).
# Keep-the-tail, not keep-the-head: a crash stack ends the stream.
STDERR_TAIL_BYTES = 8 * 1024

# Run-dir member carrying the retained (redacted) stderr tail: the Worker
# writes it at scan time (the idempotency anchor), the whole run dir ships in
# the result archive, and the Host promotes it beside events.jsonl
# (server/app/agent_broker/result_unpack.py) — one name for both sides.
AGENT_STDERR_FILENAME = "agent-stderr.log"

# Minimum lookback (characters) kept ahead of the emitted window; callers
# widen it to their longest matchable literal via ``secret_max_chars``.
REDACT_WINDOW_MARGIN = 512


class StderrTail:
    """Raw keep-the-tail stderr buffer; the redaction contract is documented
    on ``shared.pi_events.scan_and_compress_pi_events``. Trimming is batched
    (at twice the keep budget) so streaming stays linear in the stderr
    volume."""

    def __init__(self, margin: int) -> None:
        self._margin = margin
        self._keep = STDERR_TAIL_BYTES + margin
        self._parts: list[str] = []
        self._chars = 0
        self._trimmed = False

    def append(self, text: str) -> None:
        self._parts.append(text)
        self._chars += len(text)
        if self._chars > 2 * self._keep:
            self._trim()

    def _trim(self) -> None:
        text = "".join(self._parts)[-self._keep :]
        self._parts, self._chars, self._trimmed = [text], len(text), True

    def finish(self, secret_spans: SecretSpans | None) -> bytes:
        if self._chars > self._keep:
            self._trim()
        text = "".join(self._parts)
        if not text:
            return b""
        # STDERR_TAIL_BYTES characters encode to ≥ that many bytes, so this
        # window always covers the final byte cut below.
        start = max(self._margin if self._trimmed else 0, len(text) - STDERR_TAIL_BYTES)
        spans: list[Span] = []
        if secret_spans is not None:
            try:
                spans = merge_spans(secret_spans(text))
            except Exception:
                # #204 broad-except audit: 脱敏器逃逸 = fail-closed——丢弃整段
                # tail（raw stderr 不得流出本函数）。结果空间：tail 为空、锚点不
                # 落盘，error_message 退回裸退出码；压缩本身照常。日志带堆栈。
                logger.exception("Secret span callback failed; dropping the stderr tail")
                return b""
        for span_start, span_end in spans:
            if span_start < start < span_end:
                start = span_start
        data = apply_spans(text[start:], spans, offset=start).encode("utf-8", "replace")
        # 展示面：去掉末尾一个换行（tail 不以换行收尾的既有出口契约）。
        if data.endswith(b"\n"):
            data = data[:-1]
        cut = start > 0
        if len(data) > STDERR_TAIL_BYTES:
            data, cut = data[-STDERR_TAIL_BYTES:], True
        return drop_leading_partial(data) if cut else data


def drop_leading_partial(data: bytes) -> bytes:
    """After a cut, drop the leading partial line — or, on a single line,
    the leading partial token: a trim can open mid-token on an unbounded
    shape-rule secret that was never matchable whole. A window with no
    boundary at all is kept as is (already redacted)."""
    for boundary in (b"\n", b" ", b"\t"):
        index = data.find(boundary)
        if index != -1:
            return data[index + 1 :]
    return data


def persist_stderr_tail(sink: Path, tail: bytes) -> None:
    """Best-effort durable copy of the REDACTED tail (same-dir temp + replace).

    No fsync: the sink must survive process crashes (worker restart →
    restore() re-runs prepare), not power loss — page-cache write-back is
    enough for that, and the compression pass must never fail on an
    unwritable sink (the tail still rides the return value). Failure
    handling lives at the call site (scan_and_compress_pi_events). A
    failed replace removes the staging file too — a leaked
    ``.agent-stderr.*`` file in the run dir would ship in the result
    archive."""
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

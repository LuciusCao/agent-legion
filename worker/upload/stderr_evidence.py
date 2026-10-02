"""#748 crash-evidence plumbing for the agent-stderr tail.

Owns the three faces of the rescued stderr tail so ``prepare.py`` stays a
thin caller: the idempotency anchor (read back the scan-time sink file when
a re-run of prepare finds the events file already compressed — the
direct-upload fallback and worker-restart restore both re-enter prepare,
and a second scan of a rewritten file yields nothing), the error_message
summary (exit code + the tail's LAST line — a crash header ends the
stream), and the outbound redaction pass (#748 review P2: the agent env
carries instance secrets, e.g. LLM_GATEWAY_TOKEN; a crash echo quoting them
must never reach error_message / result metadata / structured events).

Redaction boundary (best-effort, deliberately): the literal pass covers
THIS process's secret-named env values plus the worker config's
``environment:`` block (register_secrets, #748 R2 P2-3); the shape pass
covers provider key prefixes (sk-/sk-ant-/ghp_/gho_) and Slack bot/user/app
tokens (``xox[bap]/``) plus JWTs and Bearer credentials. NOT covered: env
values shorter than the byte threshold, secret-named values from OTHER
machines not echoed through this process, and custom gateway tokens with
no recognizable shape — a custom-token echo in stderr survives redaction
(known best-effort boundary; the durable anchor is written ALREADY
REDACTED via the shared-sink redact callback, #748 R3 codex review P1 —
no plaintext secret ever touches disk on the anchor path).
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from shared.pi_events import STDERR_TAIL_BYTES

# Run-dir member carrying the retained agent-stderr tail. The whole run dir
# ships in the result archive, so the Host-side job dir keeps the evidence
# beside the promoted events.jsonl.
AGENT_STDERR_FILENAME = "agent-stderr.log"

# Secret-shaped literals redacted on top of the env-value pass: provider API
# key prefixes (OpenAI/Anthropic/GitHub) and Slack bot/user/app tokens plus
# JWTs. Longer alternatives first — regex alternation is ordered.
_SECRET_SHAPES = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{20,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|gho_[A-Za-z0-9]{20,}"
    r"|xox[bap]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"
)
# Bearer credentials: keep the scheme word (the crash summary stays readable
# — "Bearer ***" over a bare "***") and redact the credential itself.
_BEARER_SHAPE = re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/-]{16,}")

# Env-var name markers whose VALUES are secrets worth a literal replacement.
_SECRET_NAME_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")

# #748 R2 P3-4: the "too short to be a secret" skip uses BYTES, not chars —
# 8 CJK chars are 24 bytes of real key material. Measured on the faithful
# byte form (_secret_bytes: UTF-8 + surrogateescape, symmetric with how
# os.environ decodes), the same metric the outbound faces serialize to.
_MIN_SECRET_BYTES = 8

_REDACTED = "***"

# #748 R2 P2-3: the worker config's ``environment:`` block is the OFFICIAL
# channel that injects secrets into the agent subprocess (executor.py), but
# only ``os.environ`` was scanned — this channel had zero coverage. The
# upload lane has no handle on the executor's config object (dependency
# direction: executor → upload), so the least-polluting shape is a module
# level registry the executor feeds ONCE at startup with the config's
# environment VALUES (names are not needed — every value is treated as
# secret material; the config block exists to inject env, its values are
# exactly what a crash echo would quote).
_extra_secret_values: frozenset[str] = frozenset()
_secrets_lock = threading.Lock()


def register_secrets(values) -> None:
    """Register additional secret values for the redaction pass (idempotent).

    Fed once at worker startup (executor.py) with the config ``environment``
    block's values; thread-safe because the upload lane may already be
    delivering a restored result while the executor registers (restart
    restore submits tasks before the claim loop starts, but the registry is
    written under a lock either way)."""
    global _extra_secret_values
    with _secrets_lock:
        _extra_secret_values = _extra_secret_values | frozenset(str(value) for value in values)


def _secret_bytes(value: str) -> bytes:
    """密钥值的忠实字节形态（#755 codex P2-2）：os.environ 用文件系统策略
    解码（Linux 启动环境的非 UTF-8 字节经 surrogateescape 暴露为代理字符，
    如 \\udcff），对称的 surrogateescape 编码让字节忠实往返。严格 UTF-8
    在代理字符上抛 UnicodeEncodeError——prepare_result 对每次 agent 结果
    无条件经 max_secret_bytes 走到这里，一次逃逸即把成功执行改判 failed
    并丢弃归档。"""
    return value.encode("utf-8", "surrogateescape")


def _secret_values() -> list[str]:
    """The secret literals to replace: this process's secret-named env values
    plus the registered config-environment values, LONGEST FIRST — a short
    key that is a PREFIX of a longer key must not be replaced first and
    leave an unrecoverable tail fragment behind (#748 R2 P3-4).

    #755 codex R8 P1 对抗复审：匹配发生在解码域（流侧经 UTF-8
    errors="replace" + 通用换行翻译，尾换行属行分隔符），注册值必须落在
    同一域——CRLF/CR 形态的值补「\\n 归一」变体（CRLF PEM 经解码翻译后
    整值命中），尾空白（典型 PEM 的尾换行）补 rstrip 变体（流恰好以
    「值 − 尾换行」收尾时整值仍命中）。变体更短，最长优先排序保证完整
    值先于变体替换，不产生新残段。

    #755 codex P2-2：surrogateescape 形态的值（非 UTF-8 字节密钥）在匹配
    域呈现为其忠实字节的 errors="replace" 解码形态（U+FFFD 渲染）——只
    注册原值永不命中，等于静默丢弃该密钥（丢弃 = 泄漏通道）。每个候选
    补一条匹配域变体（忠实字节 → replace 解码；合法 UTF-8 值该变体与原
    值相同，set 去重零成本），脱敏照常覆盖该密钥。"""
    values = {
        value
        for name, value in os.environ.items()
        if any(marker in name.upper() for marker in _SECRET_NAME_MARKERS)
    }
    with _secrets_lock:
        values.update(_extra_secret_values)
    candidates: set[str] = set()
    for value in values:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        for candidate in (value, normalized, value.rstrip(), normalized.rstrip()):
            candidates.add(candidate)
            candidates.add(_secret_bytes(candidate).decode("utf-8", "replace"))
    variants = {
        candidate for candidate in candidates if len(_secret_bytes(candidate)) > _MIN_SECRET_BYTES
    }
    return sorted(
        variants,
        key=lambda value: len(_secret_bytes(value)),
        reverse=True,
    )


def max_secret_bytes() -> int:
    """已注册密钥中最长值的忠实字节数（无注册密钥时 0）。

    #755 codex P1：shared/ 的脱敏扩窗按本值对齐已注册最长密钥——固定
    512 的窗口装不下 >512 字节的密钥（PEM、长 JWT），骑跨保尾界时仍被
    先切后脱敏。调用点：prepare.py 的两处 scan_and_compress_pi_events。
    #755 codex P2-2：字节口径走 _secret_bytes（surrogateescape 对称编
    码），非 UTF-8 字节的 env 密钥不会再让本函数抛 UnicodeEncodeError。"""
    values = _secret_values()
    return len(_secret_bytes(values[0])) if values else 0


def redact_secrets(text: str) -> str:
    """Redact secret material from outbound text (best-effort, never raises).

    Three passes: (1) literal replacement of this process's secret env
    values plus the registered worker-config environment values (the exact
    strings the agent env actually carried), longest first so key-prefix
    pairs cannot leave residue; (2) Bearer credentials; (3) secret-shaped
    literals for secrets that did not come from this process (e.g.
    provider keys echoed from a child's own config). Values too short to
    be secrets (<= 8 BYTES) are skipped: replacing short literals mangles
    ordinary text for zero secrecy gain."""
    for value in _secret_values():
        text = text.replace(value, _REDACTED)
    text = _BEARER_SHAPE.sub(r"\g<1>" + _REDACTED, text)
    return _SECRET_SHAPES.sub(_REDACTED, text)


def redact_secrets_bytes(tail: bytes) -> bytes:
    """Bytes face of redact_secrets for the shared-sink callback (#748 R3,
    codex review P1): ``scan_and_compress_pi_events`` persists the stderr
    anchor BEFORE the compression rewrite, and that durable write must carry
    redacted bytes — the Worker may exit between the scan and any later
    rewrite, so the anchor on disk can never hold plaintext secrets. The
    callback decodes with replacement (the tail is bounded raw stderr),
    redacts, and re-slices to the same byte bound shared/ enforces (the
    redaction only ever SHRINKS — replacements are shorter — so the slice
    is a no-op safety net). shared/ stays stdlib-only: the callback is
    injected here, never imported there.

    #755 对抗复审 P3-2: the slice keeps the TAIL end. shared/ now hands the
    callback a window slightly wider than the bound (so a secret straddling
    the final cut is matched whole); a head slice would silently drop the
    newest bytes — the crash header the tail exists to keep."""
    return redact_secrets(tail.decode("utf-8", "replace")).encode("utf-8")[-STDERR_TAIL_BYTES:]


def stderr_tail_for_run(run_dir: Path, scanned_tail: bytes) -> bytes:
    """The idempotent stderr tail for one prepare pass (#748 review P1).

    ``scanned_tail`` is the fresh scan's capture; when it is empty but the
    scan-time sink file exists (a previous pass already rescued and
    compressed the events — direct-upload fallback, worker-restart restore),
    the file IS the tail: the compression rewrite destroyed the raw lines,
    so nothing else can recover them.

    #748 R3 (codex review P1): the sink is written ALREADY REDACTED (the
    scan's ``redact`` callback — worker side injects redact_secrets_bytes),
    so this reader only needs to slice it back to the byte bound (defensive
    against older/pre-redaction anchors) and redact the FRESH scan capture
    for its in-memory faces. The old in-place rewrite step is gone: the
    anchor never holds plaintext, so there is nothing to sanitize on read
    and no truncate-to-empty degradation path left to carry.

    #755 对抗复审 P3-3: the read-back arm no longer returns early — it
    flows through the SAME final redact pass as the fresh capture. The
    anchor is redacted at write time, but re-redacting on read is the
    defense-in-depth that covers pre-redaction anchors written by older
    Workers and any secret registered after the anchor was written."""
    tail = scanned_tail
    if not tail:
        sink = run_dir / AGENT_STDERR_FILENAME
        if sink.is_file():
            tail = sink.read_bytes()[-STDERR_TAIL_BYTES:]
    if not tail:
        return b""
    return redact_secrets(tail.decode("utf-8", "replace")).encode("utf-8")[-STDERR_TAIL_BYTES:]


def stderr_error_message(exit_code: int, stderr_tail: bytes) -> str:
    """error_message for a crashed agent process — exit code plus the
    retained stderr tail's LAST line (the crash header: a panic/trace ends
    the stream, so the newest — and most explanatory — line is the last one;
    the external API's error_summary truncates at 240 chars). The full
    multi-line tail rides the archive member + metadata; the empty tail
    keeps the legacy message unchanged.

    #748 R2 P2-2: redact FIRST, truncate AFTER. Truncating before the
    redaction left a key fragment that straddles the 200-char boundary
    un-replaced (no full-value match), leaking a secret prefix into the
    external error_message face. Redaction replaces values with ``***``
    (shorter), so the 200-char cap keeps its meaning on the redacted text.
    """
    summary = stderr_tail.decode("utf-8", "replace").strip()
    if not summary:
        return f"Agent process exited {exit_code}"
    last_line = " ".join(summary.splitlines()[-1].split())
    return f"Agent process exited {exit_code}: {redact_secrets(last_line)[:200]}"

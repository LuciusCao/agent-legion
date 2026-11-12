"""Worker-side secret registry feeding the stderr/events redaction spans.

Split out of ``worker/upload/stderr_evidence.py`` (budget reclaim, closed
anchors #843/#844): the registry face lives here — everything that reads
secret material and produces the ``shared.redaction.SecretRedactor``
snapshot — while the rescued-stderr tail helpers (anchor read-back,
error_message) stay in ``stderr_evidence.py``.

Two value sources, one snapshot:

* THIS process's secret-named ``os.environ`` values
  (``_SECRET_NAME_MARKERS``: TOKEN / KEY / SECRET / PASSWORD / CREDENTIAL).
* The worker config's ``environment:`` block, fed ONCE at worker startup
  via ``register_secrets`` (worker/executor.py; #748 R2 P2-3 — that block
  is the official channel injecting secrets into the agent subprocess).

Secret-SHAPED literals (provider key prefixes, Slack tokens, JWTs, Bearer
credentials) are redacted on top of the literal pass for secrets that did
not come from this process. Known best-effort boundary (not covered): env
values shorter than the byte threshold, other machines' secret-named values
not echoed through this process, and custom gateway tokens with no
recognizable shape.

#844: the registry is read through ``secret_snapshot()`` — ONE immutable
``shared.redaction.SecretRedactor`` carrying both the span function and the
longest-literal length. The retired call-site shape (``secret_spans`` and
``max_secret_chars`` read separately) raced ``register_secrets``: a value
registered between the two reads widened the span function but not the
lookback margin, so a cut could split a secret the spans would have
matched whole. The snapshot's ``max_chars`` is by construction ≥ the
longest matchable literal — the lookback contract, now enforced where the
values are read instead of trusted at the call site.
"""

from __future__ import annotations

import os
import re
import threading

from shared.redaction import SecretRedactor, Span

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
_BEARER_SHAPE = re.compile(r"\b(Bearer\s+)([A-Za-z0-9._~+/-]{16,})")

# Env-var name markers whose VALUES are secrets worth a literal replacement.
_SECRET_NAME_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")

# #748 R2 P3-4: the "too short to be a secret" skip uses BYTES, not chars —
# 8 CJK chars are 24 bytes of real key material. Measured on the faithful
# byte form (_secret_bytes: UTF-8 + surrogateescape, symmetric with how
# os.environ decodes), the same metric the outbound faces serialize to.
_MIN_SECRET_BYTES = 8

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
    无条件经 secret_snapshot 的密钥长度计算走到这里，一次逃逸即把成功执行
    改判 failed 并丢弃归档。"""
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
    return sorted(variants, key=lambda value: len(_secret_bytes(value)), reverse=True)


def _value_spans(values: list[str], text: str) -> list[Span]:
    """Spans of secret material in ``text`` against ONE frozen value list —
    the snapshot's span function. Three rule families, all reported as
    spans and merged by the caller: (1) every occurrence of each literal
    value (a short key that prefixes or nests inside a longer one is
    covered by the merge instead of leaving residue); (2) Bearer
    credentials (the scheme word stays readable); (3) secret-shaped
    literals for secrets that did not come from this process (e.g.
    provider keys echoed from a child's own config). Values too short to
    be secrets (<= 8 BYTES) never enter ``values``."""
    spans: list[Span] = []
    for value in values:
        start = 0
        while (index := text.find(value, start)) != -1:
            spans.append((index, index + len(value)))
            start = index + 1
    spans.extend(match.span(2) for match in _BEARER_SHAPE.finditer(text))
    spans.extend(match.span() for match in _SECRET_SHAPES.finditer(text))
    return spans


def secret_snapshot() -> SecretRedactor:
    """The registry as ONE immutable read (#844).

    The span function and the longest-literal length come from the SAME
    ``_secret_values()`` list, so the lookback margin a text-cutting caller
    keeps (``max(REDACT_WINDOW_MARGIN, snapshot.max_chars)``) is by
    construction ≥ every literal the span function can match — the retired
    two-read shape could observe the registry at two different times and
    register a longer value in between. A later ``register_secrets``
    produces a NEW snapshot; one already in hand never changes. Single-shot
    faces (error_message, the anchor read-back) take a fresh snapshot per
    call — they do not cut text, so there is no margin to keep consistent.
    """
    values = _secret_values()
    max_chars = max((len(value) for value in values), default=0)
    return SecretRedactor(lambda text: _value_spans(values, text), max_chars)

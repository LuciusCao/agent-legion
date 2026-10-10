"""单行渲染面：一条 pump 事件行 → 一条物理行的取证转储文本（#1147 /
#1168 P2/F4）。

从 ``worker/state_evidence.py`` 拆出的姊妹模块（按被测主题）：应急转储
sink 的逐行渲染——先脱敏后截断（#748 纪律），一条 framed 行永远是一条
物理行（评审 P3-4：占位标记内联无换行，逐行 ``json.loads`` 的取证工具
不会把后续行误当续行）。

#1168 F4：判定为 JSON 的事件行超长（> ``MAX_DUMP_LINE_CHARS``）时**不再
中切**——截断点落在 ``\\uD83D`` 转义序列或字符串边界中间时产物不再是
合法 JSON，逐行解析工具整行报废。改按「截字段值 + 重序列化」：递归把
每个字符串值截到递减 cap（8 KiB 起，对半降到 64），序列化结果落在限内
即收；结构本身超限（海量小字段）才整体替换为单行占位事件
``{"truncated": true, "original_bytes": N}``——两种形态都保证
``json.loads`` 可解析。非 JSON 行保持既有中切标记形态。
"""

from __future__ import annotations

import json
from typing import Any

from shared.redaction import SecretRedactor

MAX_DUMP_LINE_CHARS = 64 * 1024
# 结构超限时的占位事件：仍是合法单行 JSON（json.dumps 无换行），取证工具
# 逐行解析不报废，original_bytes 保留原始尺度信息。
_TRUNCATION_CAPS = (8192, 4096, 2048, 1024, 512, 256, 128, 64)


def render_line(line: bytes, redactor: SecretRedactor) -> str:
    """One framed line as redacted text: JSON events keep their structure
    (``redact_json`` — the same function the delivery path applies to
    archived events), non-JSON lines get span redaction. Capping happens
    AFTER redaction (redact first, cut after — the #748 discipline) and
    keeps one framed line ONE physical line (P3-4) and, for JSON lines,
    the line PARSEABLE (#1168 F4 — see the module docstring)."""
    text = line.decode("utf-8", "replace")
    try:
        event = json.loads(text)
    except ValueError:
        event = None
    if isinstance(event, dict):
        redacted = redactor.redact_json(event)
        out = (
            text
            if redacted == event
            else json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
        )
        if len(out) > MAX_DUMP_LINE_CHARS:
            out = bounded_json_line(redacted, len(line))
    else:
        out = redactor.redact(text)
        if len(out) > MAX_DUMP_LINE_CHARS:
            keep = MAX_DUMP_LINE_CHARS // 2
            dropped = len(out) - MAX_DUMP_LINE_CHARS
            out = f"{out[:keep]}[...{dropped} chars truncated...]{out[-keep:]}"
    return out + "\n"


def bounded_json_line(event: dict[str, Any], original_bytes: int) -> str:
    """One over-cap JSON event re-serialized with its string values capped
    (#1168 F4): the cap halves until the serialization fits
    ``MAX_DUMP_LINE_CHARS``; a structure that never fits (huge numbers of
    small fields) degrades to the single-line placeholder event, never to a
    syntactically broken middle-cut."""
    for cap in _TRUNCATION_CAPS:
        out = json.dumps(_capped_strings(event, cap), ensure_ascii=False, separators=(",", ":"))
        if len(out) <= MAX_DUMP_LINE_CHARS:
            return out
    return json.dumps({"truncated": True, "original_bytes": original_bytes}, separators=(",", ":"))


def _capped_strings(value: Any, cap: int) -> Any:
    """``value`` with every nested string VALUE truncated to ``cap`` chars
    (inline marker, no newline); structure and non-string leaves pass
    through — same shape contract as ``redact_json``."""
    if isinstance(value, str):
        if len(value) <= cap:
            return value
        dropped = len(value) - cap
        return f"{value[:cap]}[...{dropped} chars truncated...]"
    if isinstance(value, list):
        return [_capped_strings(item, cap) for item in value]
    if isinstance(value, dict):
        return {key: _capped_strings(item, cap) for key, item in value.items()}
    return value

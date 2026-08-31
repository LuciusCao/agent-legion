"""openclaw 一次性结果 → pi 兼容子集事件合成（issue #75 阶段 3，单文件翻译层）。

实测（OpenClaw 2026.6.11，dist 源码 + 官方 docs/cli/agent.md 核验）：
``openclaw agent --json`` 的 stdout 是**一次性 pretty-printed 结果
envelope**（``{payloads: [{text, mediaUrl?, isError?}], meta: {...}}``），
诊断走 stderr——不是 pi 兼容的流式 JSONL 事件流，没有中间事件可翻译。
Worker 执行侧把 stderr 合并进 stdout（worker/execution/run.py），所以
捕获流 = 诊断行 + envelope 的混合。进程退出后本模块把 envelope 翻译为
pi 子集事件并**追加**进 events.jsonl（原始捕获保留便于排障；上传压缩只
留 ``shared/pi_events.RELEVANT_EVENT_TYPES``，非 JSON 行自然淘汰）。

事件形状对齐 ``velites/schema/events.schema.json`` 与
``shared/pi_model_error`` 的失败检测约定：``errorMessage`` +
``stopReason: "error"`` 让 ``detect_model_error`` 能 surfaced LLM 失败；
SIGTERM/SIGINT 退出码合成 ``agent_end{reason: cancelled}``（对齐 velites
优雅退出语义）。envelope 无 token usage 字段，计量为空。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# SIGTERM/SIGINT 及其 shell 映射退出码（对齐 wait_for_exit 的取消路径）。
_CANCEL_EXIT_CODES = frozenset({-15, -2, 130, 143})
# 无 envelope 时带进 errorMessage 的诊断尾部上限（认证失败等场景）。
_MAX_DIAGNOSTIC_CHARS = 2000


def _extract_envelope(text: str) -> dict[str, Any] | None:
    """从捕获流尾部定位一次性 JSON envelope（pretty-printed 多行或单行）。

    从后往前试每个以 ``{`` 开头的行作为 envelope 起点、每个以 ``}`` 结尾的
    行作为终点做 json.loads；要求含 payloads/meta/result 键以防误中诊断里的
    JSON 日志行。envelope 通常在流末尾，候选一两次即命中。
    """
    lines = text.splitlines()
    for start in range(len(lines) - 1, -1, -1):
        if not lines[start].lstrip().startswith("{"):
            continue
        for end in range(len(lines), start, -1):
            if not lines[end - 1].rstrip().endswith("}"):
                continue
            try:
                data = json.loads("\n".join(lines[start:end]))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and any(k in data for k in ("payloads", "meta", "result")):
                return data
    return None


def envelope_to_events(
    envelope: dict[str, Any], *, session_id: str, timestamp: int
) -> list[dict[str, Any]]:
    """把一次性 envelope 翻译为 pi 子集事件序列（成功或模型失败）。"""
    texts: list[str] = []
    error_message = ""
    for payload in envelope.get("payloads") or []:
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            continue
        if payload.get("isError"):
            error_message = error_message or text
        else:
            texts.append(text)
    if not error_message:
        meta_error = (envelope.get("meta") or {}).get("error")
        if isinstance(meta_error, str) and meta_error:
            error_message = meta_error
        elif isinstance(meta_error, dict) and meta_error.get("message"):
            error_message = str(meta_error["message"])
    content = [{"type": "text", "text": text} for text in texts]
    if error_message:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "stopReason": "error",
            "errorMessage": error_message,
        }
        agent_end: dict[str, Any] = {"type": "agent_end", "error": error_message}
    else:
        message = {"role": "assistant", "content": content, "stopReason": "stop"}
        agent_end = {"type": "agent_end"}
    return [
        {"type": "session", "sessionId": session_id, "timestamp": timestamp},
        {"type": "agent_start"},
        {"type": "turn_start", "turnIndex": 1},
        {"type": "message_start", "message": {"role": "assistant", "content": []}},
        {"type": "message_end", "message": message},
        {"type": "turn_end", "turnIndex": 1},
        agent_end,
    ]


def failure_events(
    *, session_id: str, timestamp: int, exit_code: int, diagnostics: str
) -> list[dict[str, Any]]:
    """无 envelope（启动失败/认证失败/被取消）时的兜底合成。"""
    if exit_code in _CANCEL_EXIT_CODES:
        return [
            {"type": "session", "sessionId": session_id, "timestamp": timestamp},
            {"type": "agent_start"},
            {"type": "agent_end", "reason": "cancelled"},
        ]
    error = diagnostics[-_MAX_DIAGNOSTIC_CHARS:].strip() or f"openclaw exited with code {exit_code}"
    return envelope_to_events(
        {"payloads": [{"text": error, "isError": True}]},
        session_id=session_id,
        timestamp=timestamp,
    )


def synthesize_openclaw_events(events_path: Path, *, session_id: str, exit_code: int) -> None:
    """进程退出后读捕获流，把合成事件追加进 events.jsonl（失败静默不炸上报链）。"""
    try:
        captured = events_path.read_text(encoding="utf-8", errors="replace")
        timestamp = int(time.time())
        envelope = _extract_envelope(captured)
        if envelope is not None:
            events = envelope_to_events(envelope, session_id=session_id, timestamp=timestamp)
        elif exit_code == 0:
            # exit 0 但无 envelope（异常形态）：按成功合成空内容，产物校验兜底。
            events = envelope_to_events(
                {"payloads": []}, session_id=session_id, timestamp=timestamp
            )
        else:
            events = failure_events(
                session_id=session_id,
                timestamp=timestamp,
                exit_code=exit_code,
                diagnostics=captured,
            )
        with events_path.open("a", encoding="utf-8") as dst:
            for event in events:
                dst.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        # 合成失败不阻断结果上报：exit_code 与产物校验仍是权威判定。
        return

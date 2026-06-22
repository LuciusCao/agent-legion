from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

MAX_DETAIL_LEN = 800
TRUNCATION_HINT = "\n... (已截断，下载原始日志可查看完整内容)"


class LogEntry(TypedDict):
    type: str
    title: str
    detail: str
    truncated: bool


def _truncate(text: str, limit: int = MAX_DETAIL_LEN) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + TRUNCATION_HINT, True


def _extract_text(
    content: list[dict[str, Any]] | dict[str, Any] | None,
) -> str:
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            parts.append(str(item.get("text", "")))
        elif item_type == "thinking":
            parts.append(str(item.get("thinking", "")))
    return "\n".join(parts)


def _format_tool_arguments(args: Any) -> str:
    if isinstance(args, dict):
        return json.dumps(args, ensure_ascii=False, indent=2)
    return str(args)


def _format_tool_call(tool_call: dict[str, Any]) -> str:
    name = tool_call.get("name") or "tool"
    arguments = tool_call.get("arguments")
    return f"{name}({_format_tool_arguments(arguments)})"


def _format_tool_result(result: dict[str, Any]) -> str:
    content = result.get("content")
    text = _extract_text(content)
    if text:
        return text
    # Fallback to a compact JSON preview when the result is not plain text.
    return json.dumps(content, ensure_ascii=False, indent=2)[:MAX_DETAIL_LEN]


def _collect_stderr_lines(events_path: Path) -> list[str]:
    stderr_path = events_path.with_name("stderr.log")
    if not stderr_path.is_file():
        return []
    text = stderr_path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()]
    return lines


def _parse_pi_events(events_path: Path) -> list[LogEntry]:
    """Compress a Pi JSONL event stream into human-readable turns."""
    entries: list[LogEntry] = []
    turn_number = 0
    non_json_lines: list[str] = []

    with events_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                non_json_lines.append(line)
                continue

            event_type = event.get("type")
            if event_type != "turn_end":
                continue

            turn_number += 1
            message = event.get("message") or {}
            content = message.get("content") or []
            if not isinstance(content, list):
                content = []

            thinking = ""
            tool_calls: list[dict[str, Any]] = []
            assistant_texts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type == "thinking":
                    thinking = str(item.get("thinking", ""))
                elif item_type == "toolCall":
                    tool_calls.append(item)
                elif item_type == "text":
                    assistant_texts.append(str(item.get("text", "")))

            if thinking:
                detail, truncated = _truncate(thinking)
                entries.append(
                    {
                        "type": "thinking",
                        "title": f"Turn {turn_number} · 思考",
                        "detail": detail,
                        "truncated": truncated,
                    }
                )

            for tool_call in tool_calls:
                entries.append(
                    {
                        "type": "tool_call",
                        "title": f"Turn {turn_number} · 工具调用 {tool_call.get('name', 'tool')}",
                        "detail": _format_tool_call(tool_call),
                        "truncated": False,
                    }
                )

            for tool_result in event.get("toolResults") or []:
                if not isinstance(tool_result, dict):
                    continue
                detail, truncated = _truncate(_format_tool_result(tool_result))
                entries.append(
                    {
                        "type": "tool_result",
                        "title": f"Turn {turn_number} · 工具结果 {tool_result.get('toolName', 'tool')}",
                        "detail": detail,
                        "truncated": truncated,
                    }
                )

            if assistant_texts:
                detail, truncated = _truncate("\n".join(assistant_texts))
                entries.append(
                    {
                        "type": "message",
                        "title": f"Turn {turn_number} · 回复",
                        "detail": detail,
                        "truncated": truncated,
                    }
                )

            stop_reason = message.get("stopReason")
            error_message = message.get("errorMessage") or ""
            if stop_reason == "error" or error_message:
                entries.append(
                    {
                        "type": "error",
                        "title": f"Turn {turn_number} · 模型调用错误",
                        "detail": error_message or f"stop_reason={stop_reason}",
                        "truncated": False,
                    }
                )

    stderr_lines = _collect_stderr_lines(events_path)
    if stderr_lines:
        detail, truncated = _truncate("\n".join(stderr_lines))
        entries.append(
            {
                "type": "stderr",
                "title": "标准错误输出",
                "detail": detail,
                "truncated": truncated,
            }
        )

    if non_json_lines and not entries:
        # The file did not contain recognizable Pi events; show the raw text.
        detail, truncated = _truncate("\n".join(non_json_lines))
        entries.append(
            {
                "type": "raw",
                "title": "原始日志",
                "detail": detail,
                "truncated": truncated,
            }
        )

    return entries


def render_log(
    log_path: Path,
    run_dir: Path | None,
    sanitize: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Return a human-readable log and optional structured entries for a node run."""
    structured: list[LogEntry] = []
    truncated = False

    events_path: Path | None = None
    if run_dir is not None:
        candidate = run_dir / "events.jsonl"
        if candidate.is_file():
            events_path = candidate
    if events_path is None and log_path.name == "events.jsonl" and log_path.is_file():
        events_path = log_path
    if events_path is not None:
        structured = _parse_pi_events(events_path)

    if structured:
        log_lines: list[str] = []
        for entry in structured:
            log_lines.append(f"## {entry['title']}")
            log_lines.append(entry["detail"])
            log_lines.append("")
        log_text = "\n".join(log_lines)
        if sanitize is not None:
            log_text = sanitize(log_text)
        return {"log": log_text, "structured": structured, "truncated": False}

    if not log_path.is_file():
        return {"log": "", "structured": [], "truncated": False}

    text = log_path.read_text(encoding="utf-8", errors="replace")
    if sanitize is not None:
        text = sanitize(text)

    # Preserve the existing tail + return truncation for large unstructured logs
    # so the frontend never has to render a multi-megabyte raw payload.
    TAIL_LIMIT = 12 * 1024
    RETURN_LIMIT = 8 * 1024
    encoded = text.encode("utf-8")
    if len(encoded) > TAIL_LIMIT:
        text = encoded[-TAIL_LIMIT:].decode("utf-8", errors="ignore")
        truncated = True
    encoded = text.encode("utf-8")
    if len(encoded) > RETURN_LIMIT:
        text = encoded[:RETURN_LIMIT].decode("utf-8", errors="ignore")
        truncated = True

    return {"log": text, "structured": [], "truncated": truncated}

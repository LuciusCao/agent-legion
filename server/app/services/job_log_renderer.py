from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict


class LogEntry(TypedDict):
    type: str
    title: str
    detail: str
    truncated: bool


def _extract_text(
    content: list[Any] | dict[str, Any] | None,
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
        if item_type in ("text", "thinking"):
            parts.append(str(item.get(item_type) or ""))
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
    detail = text or json.dumps(content, ensure_ascii=False, indent=2)
    if result.get("isError"):
        detail = "[ERROR]\n" + detail
    return detail


def _collect_stderr_lines(events_path: Path) -> list[str]:
    stderr_path = events_path.with_name("stderr.log")
    if not stderr_path.is_file():
        return []
    text = stderr_path.read_text(encoding="utf-8", errors="replace")
    return [line for line in text.splitlines() if line.strip()]


def _parse_command(command_json: str) -> list[str]:
    try:
        command = json.loads(command_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
        return []
    return command


def _parse_pi_events(events_path: Path, agent_start_detail: str = "") -> list[LogEntry]:
    """Compress a Pi JSONL event stream into a human-readable turn chain.

    The real event stream uses ``message_start`` / ``message_update`` /
    ``message_end``. We only read the final ``message_end`` snapshots and
    ignore the thousands of streaming deltas in between.
    """
    entries: list[LogEntry] = []
    turn_number = 0
    non_json_lines: list[str] = []
    # Real Pi output is compact; tests and pretty-printed streams may include
    # whitespace after the colon. Match both forms without parsing every line.
    _RELEVANT_EVENT_RE = re.compile(r'"type"\s*:\s*"(agent_start|turn_start|message_end)"')

    with events_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            # The Pi stream contains millions of ``message_update`` deltas and
            # other events we do not render. Avoid parsing them.
            if not _RELEVANT_EVENT_RE.search(line):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                non_json_lines.append(line)
                continue

            event_type = event.get("type")
            if event_type == "agent_start":
                entries.append(
                    {
                        "type": "session",
                        "title": "Agent 开始运行",
                        "detail": agent_start_detail,
                        "truncated": False,
                    }
                )
                continue
            if event_type == "turn_start":
                turn_number += 1
                continue
            if event_type != "message_end":
                continue

            message = event.get("message") or {}
            role = message.get("role")
            content = message.get("content") or []
            if not isinstance(content, list):
                content = []

            if role == "assistant":
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type")
                    if item_type == "thinking":
                        detail = str(item.get("thinking", ""))
                        entries.append(
                            {
                                "type": "thinking",
                                "title": f"Turn {turn_number} · 思考",
                                "detail": detail,
                                "truncated": False,
                            }
                        )
                    elif item_type == "toolCall":
                        detail = _format_tool_call(item)
                        entries.append(
                            {
                                "type": "tool_call",
                                "title": f"Turn {turn_number} · 工具调用 {item.get('name', 'tool')}",
                                "detail": detail,
                                "truncated": False,
                            }
                        )
                    elif item_type == "text":
                        text = str(item.get("text", "")).strip()
                        if text:
                            entries.append(
                                {
                                    "type": "message",
                                    "title": f"Turn {turn_number} · 回复",
                                    "detail": text,
                                    "truncated": False,
                                }
                            )

                stop_reason = message.get("stopReason")
                error_message = message.get("errorMessage") or ""
                if (stop_reason and stop_reason not in ("stop", "toolUse")) or error_message:
                    detail = error_message or f"stop_reason={stop_reason}"
                    entries.append(
                        {
                            "type": "error",
                            "title": f"Turn {turn_number} · 模型调用错误",
                            "detail": detail,
                            "truncated": False,
                        }
                    )

            elif role == "toolResult":
                tool_name = message.get("toolName") or "tool"
                tool_call_id = message.get("toolCallId", "")[:8]
                title = f"Turn {turn_number} · 工具结果 {tool_name}"
                if tool_call_id:
                    title += f" ({tool_call_id})"
                detail = _format_tool_result(message)
                entries.append(
                    {
                        "type": "tool_result",
                        "title": title,
                        "detail": detail,
                        "truncated": False,
                    }
                )

    stderr_lines = _collect_stderr_lines(events_path)
    if stderr_lines:
        entries.append(
            {
                "type": "stderr",
                "title": "标准错误输出",
                "detail": "\n".join(stderr_lines),
                "truncated": False,
            }
        )

    if non_json_lines and not entries:
        entries.append(
            {
                "type": "raw",
                "title": "原始日志",
                "detail": "\n".join(non_json_lines),
                "truncated": False,
            }
        )

    return entries


def render_log(
    log_path: Path,
    run_dir: Path | None,
    sanitize: Callable[[str], str] | None = None,
    command_json: str = "[]",
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
        start_parts: list[str] = []
        command = _parse_command(command_json)
        if command:
            start_parts.extend(["启动命令", shlex.join(command)])
        if run_dir is not None:
            prompt_path = run_dir / "prompt.md"
            if prompt_path.is_file():
                prompt = prompt_path.read_text(encoding="utf-8", errors="replace")
                start_parts.extend(["提示词", prompt])
        structured = _parse_pi_events(events_path, "\n".join(start_parts))

    if sanitize is not None:
        for entry in structured:
            entry["title"] = sanitize(entry["title"])
            entry["detail"] = sanitize(entry["detail"])

    if structured:
        log_lines: list[str] = []
        for entry in structured:
            log_lines.append(f"## {entry['title']}")
            log_lines.append(entry["detail"])
            log_lines.append("")
        log_text = "\n".join(log_lines)
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

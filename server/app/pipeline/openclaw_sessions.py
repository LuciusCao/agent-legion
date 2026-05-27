import json
from pathlib import Path
from typing import Any


def openclaw_sessions_dir(agent_id: str) -> Path:
    agent = agent_id or "main"
    return Path.home() / ".openclaw" / "agents" / agent / "sessions"


def resolve_openclaw_session_path(agent_id: str, session_id: str) -> Path | None:
    if not session_id:
        return None
    if "/" in session_id or session_id.endswith(".jsonl"):
        path = Path(session_id)
        return path if path.exists() else None

    base = openclaw_sessions_dir(agent_id)
    exact = base / f"{session_id}.jsonl"
    if exact.exists():
        return exact

    if not base.exists():
        return None
    matches = [
        path
        for path in base.glob("*.jsonl")
        if session_id in path.name and not path.name.endswith(".trajectory.jsonl")
    ]
    return matches[0] if len(matches) == 1 else None


def _message_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in message.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
        elif item.get("type") == "toolCall":
            name = item.get("name") or "tool"
            parts.append(f"[tool call] {name}")
    return "\n".join(parts)


def render_openclaw_session(path: Path, limit: int = 20000) -> str:
    lines: list[str] = [f"Session file: {path}", ""]
    with path.open("r", encoding="utf-8") as file:
        for raw_line in file:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") == "session":
                sid = obj.get("id", "")
                cwd = obj.get("cwd", "")
                lines.append(f"[SESSION] {sid} cwd={cwd}".strip())
                continue

            if obj.get("type") != "message":
                continue
            message = obj.get("message") or {}
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "unknown").upper()
            text = _message_text(message)
            if text:
                lines.append(f"[{role}]\n{text}")

    rendered = "\n\n".join(lines).strip()
    if len(rendered) > limit:
        return rendered[-limit:]
    return rendered

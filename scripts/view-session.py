#!/usr/bin/env python3
"""Render an OpenClaw session JSONL into a human-readable conversation log.

Usage:
    uv run scripts/view-session.py <session-id>
    uv run scripts/view-session.py <session-id> --full
    uv run scripts/view-session.py <session-id> --no-results
"""

import argparse
import json
import sys
from pathlib import Path


def sessions_dir() -> Path:
    return Path.home() / ".openclaw/agents/main/sessions"


def resolve_session_path(session_id: str) -> Path:
    """Find session file by exact id, partial match, or path."""
    if "/" in session_id or session_id.endswith(".jsonl"):
        p = Path(session_id)
        if p.exists():
            return p

    base = sessions_dir()
    exact = base / f"{session_id}.jsonl"
    if exact.exists():
        return exact

    candidates = [
        p for p in base.glob("*.jsonl")
        if session_id in p.name and not p.name.endswith(".trajectory.jsonl")
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        print(f"Ambiguous session id '{session_id}' matches:", file=sys.stderr)
        for c in candidates[:10]:
            print(f"  {c.name}", file=sys.stderr)
        raise SystemExit(1)

    print(f"Session not found: {session_id}", file=sys.stderr)
    raise SystemExit(1)


def truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} more chars)"


def color(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text: str) -> str:
    return color(text, "1")


def dim(text: str) -> str:
    return color(text, "90")


def cyan(text: str) -> str:
    return color(text, "36")


def yellow(text: str) -> str:
    return color(text, "33")


def green(text: str) -> str:
    return color(text, "32")


def magenta(text: str) -> str:
    return color(text, "35")


def red(text: str) -> str:
    return color(text, "31")


def list_sessions(args: argparse.Namespace) -> None:
    base = sessions_dir()
    files = [
        p for p in base.glob("*.jsonl")
        if not p.name.endswith(".trajectory.jsonl")
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    print(f"{bold('Recent OpenClaw Sessions')} ({len(files)} total, showing top {args.limit})\n")
    print(f"{'Session ID':<50} {'Age':>10} {'Size':>8}")
    print("-" * 70)

    import time as time_mod

    for p in files[:args.limit]:
        sid = p.stem
        mtime = p.stat().st_mtime
        age_sec = time_mod.time() - mtime
        if age_sec < 60:
            age_str = f"{int(age_sec)}s"
        elif age_sec < 3600:
            age_str = f"{int(age_sec / 60)}m"
        elif age_sec < 86400:
            age_str = f"{int(age_sec / 3600)}h"
        else:
            age_str = f"{int(age_sec / 86400)}d"

        size = p.stat().st_size
        if size < 1024:
            size_str = f"{size}B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f}K"
        else:
            size_str = f"{size / (1024 * 1024):.1f}M"

        # Try to extract video_id hint from session id
        hint = ""
        if sid.startswith("knowledge_") or sid.startswith("question_"):
            parts = sid.split("-")
            if len(parts) >= 2:
                hint = f"  {dim(parts[0])}"

        print(f"{sid:<50} {age_str:>10} {size_str:>8}{hint}")


def render_message(obj: dict, args: argparse.Namespace) -> None:
    msg = obj.get("message", {})
    role = msg.get("role", "unknown")
    content = msg.get("content", [])

    if role == "user":
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "").strip()
                if text:
                    print(f"{bold('[USER]')} {text}")

    elif role == "assistant":
        has_text = False
        for item in content:
            itype = item.get("type")
            if itype == "text":
                text = item.get("text", "").strip()
                if text:
                    has_text = True
                    print(f"{bold('[ASSISTANT]')} {text}")
            elif itype == "toolCall":
                name = item.get("name", "?")
                tid = item.get("id", "")[:8]
                arguments = item.get("arguments", {})
                arg_str = json.dumps(arguments, ensure_ascii=False, indent=2)
                arg_preview = arg_str.replace("\n", " ") if "\n" in arg_str else arg_str
                if len(arg_preview) > 120:
                    arg_preview = arg_preview[:117] + "..."
                print(f"  {dim('→')} {cyan(name)} {dim(tid)} {arg_preview}")
        if not has_text and any(i.get("type") == "toolCall" for i in content):
            # Assistant message with only tool calls: print a minimal header
            pass

    elif role == "toolResult":
        if args.no_results:
            return
        name = msg.get("toolName", "?")
        tid = msg.get("toolCallId", "")[:8]
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "")
                limit = 0 if args.full else 500
                preview = truncate(text, limit)
                status = ""
                if msg.get("isError"):
                    status = red(" [ERROR]")
                print(f"  {dim('←')} {green(name)} {dim(tid)}{status}")
                if preview:
                    for line in preview.splitlines():
                        print(f"      {dim(line)}")


def render_event(obj: dict, _args: argparse.Namespace) -> None:
    etype = obj.get("type", "")
    if etype == "thinking_level_change":
        level = obj.get("thinkingLevel", "?")
        print(f"{magenta('[CONFIG]')} thinking level = {yellow(level)}")
    elif etype == "model_change":
        provider = obj.get("provider", "?")
        model = obj.get("modelId", "?")
        print(f"{magenta('[CONFIG]')} model = {provider}/{model}")
    elif etype == "session":
        sid = obj.get("id", "?")
        cwd = obj.get("cwd", "?")
        print(f"{magenta('[SESSION]')} {sid}  cwd={cwd}")


def main() -> None:
    parser = argparse.ArgumentParser(description="View OpenClaw session conversation")
    parser.add_argument("session_id", nargs="?", help="Session id, partial match, or file path")
    parser.add_argument("--full", action="store_true", help="Show full tool results without truncation")
    parser.add_argument("--no-results", action="store_true", help="Hide tool results")
    parser.add_argument("--list", action="store_true", help="List recent sessions instead of viewing one")
    parser.add_argument("--limit", type=int, default=20, help="Max sessions to list (default: 20)")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    args = parser.parse_args()

    if args.no_color:
        global sys
        # Force non-tty so color() returns plain text
        class FakeStdout:
            def isatty(self):
                return False
            def __getattr__(self, name):
                return getattr(sys.stdout, name)
        sys.stdout = FakeStdout()  # type: ignore[assignment]

    if args.list:
        list_sessions(args)
        return

    if not args.session_id:
        print("Error: session_id is required (or use --list)", file=sys.stderr)
        raise SystemExit(1)

    path = resolve_session_path(args.session_id)
    print(f"{dim('Session file:')} {path}")
    print()

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = obj.get("type", "")
            if etype == "message":
                render_message(obj, args)
            else:
                render_event(obj, args)


if __name__ == "__main__":
    main()

"""Read-only tail of the Kimi Code (0.43+) main-agent wire journal (#938).

Kimi Code's engine launches turns on its own when a background task
notification or a cron fire arrives while the session is idle, but its ACP
adapter forwards only the events of the turn bound to the in-flight
``session/prompt`` driver; events of every other turn are dropped before the
wire. The journal under ``$KIMI_CODE_HOME`` is then the only observable
record of such a turn. Layout (Kimi Code 0.43):
``sessions/<workspace-id>/<acp-session-id>/agents/main/wire.jsonl`` — one JSON
record per line, append-only.

Reads are bounded, descriptor-anchored (no symlink traversal) and start at the
end of the file as first observed: history is never replayed into the Studio
timeline. A replaced or truncated journal re-baselines at its new end — a
possible miss, never a duplicate.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from server.app.studio_chat.task_metadata_files import directory

MAX_READ_BYTES = 1 << 20
_SESSION_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
_WIRE_PARTS = ("agents", "main", "wire.jsonl")


def kimi_code_homes(cwd: str) -> list[Path]:
    """Candidate homes: the ACP SDK spawns the agent with a trimmed environment
    (HOME/PATH/...), so a plain ``kimi acp`` child uses ``~/.kimi-code``; a
    wrapper command may pin another home, which the server learns from its own
    ``KIMI_CODE_HOME``. ACP session ids are unique, so probing both is safe."""
    homes: list[Path] = []
    for raw in (os.environ.get("KIMI_CODE_HOME"), str(Path.home() / ".kimi-code")):
        if not raw:
            continue
        path = Path(raw) if Path(raw).is_absolute() else Path(cwd) / raw
        if path.resolve() not in homes:
            homes.append(path.resolve())
    return homes


def locate_wire(homes: list[Path], session_id: str) -> Path | None:
    """The main-agent journal of one ACP session, or None until it exists."""
    if not _SESSION_ID.fullmatch(session_id):
        return None
    for home in homes:
        sessions = home / "sessions"
        try:
            names = sorted(os.listdir(sessions))
        except OSError:
            continue
        for name in names:
            if name.startswith(".") or not _SESSION_ID.fullmatch(name):
                continue
            candidate = sessions.joinpath(name, session_id, *_WIRE_PARTS)
            if candidate.is_file():
                return candidate
    return None


class WireTail:
    """Incremental complete-line reader; ``read`` never raises on bad lines."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.identity: tuple[int, int] | None = None

    def read(self) -> list[dict[str, Any]]:
        with directory(self.path.parent) as parent:
            descriptor = os.open(
                self.path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent
            )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("kimi wire journal is not a regular file")
            identity = (info.st_dev, info.st_ino)
            if identity != self.identity or info.st_size < self.offset:
                # First sight, replacement or truncation: start at the end.
                self.identity, self.offset = identity, info.st_size
                return []
            if info.st_size == self.offset:
                return []
            data = os.pread(
                descriptor, min(info.st_size - self.offset, MAX_READ_BYTES), self.offset
            )
        finally:
            os.close(descriptor)
        end = data.rfind(b"\n")
        if end < 0:
            if len(data) >= MAX_READ_BYTES:
                # One oversized line: skip it; the remainder fails to parse.
                self.offset += len(data)
            return []
        self.offset += end + 1
        records: list[dict[str, Any]] = []
        for line in data[:end].split(b"\n"):
            try:
                value = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

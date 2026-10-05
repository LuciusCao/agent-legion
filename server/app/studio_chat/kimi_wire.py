"""Read-only tail of the Kimi Code (0.43+) main-agent wire journal (#938).

Kimi Code's engine launches turns on its own when a background task
notification or a cron fire arrives while the session is idle, but its ACP
adapter forwards only the events of the turn bound to the in-flight
``session/prompt`` driver; events of every other turn are dropped before the
wire. The journal under ``$KIMI_CODE_HOME`` is then the only observable
record of such a turn. Layout (Kimi Code 0.43):
``sessions/<workspace-id>/<acp-session-id>/agents/main/wire.jsonl`` — one JSON
record per line, append-only.

Reads are bounded and descriptor-anchored through ``fs_safety`` (no symlink
traversal, no hard-linked or non-regular journal).

Where reading starts is never "the end at some instant" — every instant has a
before, and a turn written there would be mistaken for history (#938 review
R1/R2). Instead:

* A journal this runtime's own agent process created (session/new, i.e. not
  ``loaded_existing``) belongs wholly to this runtime: read from its start.
  Studio-driven turns are skipped by origin, so nothing duplicates.
* A loaded journal (resume via session/load) is baselined by
  ``wire_baseline.capture_wire_baseline`` while nobody can write it: after the
  previous agent process was reaped and before the new one is spawned
  (resume.py).
  Everything the new process writes — during session/load, before on_ready,
  before the first poll — lands after that baseline.
* A loaded journal without a usable baseline is baselined at first sight
  (stat only): a possible miss, never a replay. A replaced or truncated
  journal re-baselines at its new end — a possible miss, never a duplicate.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.fs_safety import open_dir_nofollow as directory
from server.app.fs_safety import open_regular_at

if TYPE_CHECKING:
    from server.app.studio_chat.wire_baseline import WireBaseline

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


def session_dirs(homes: list[Path], session_id: str) -> list[Path]:
    """``<home>/sessions/<workspace-id>/<session_id>`` candidates (unchecked)."""
    if not _SESSION_ID.fullmatch(session_id):
        return []
    candidates: list[Path] = []
    for home in homes:
        sessions = home / "sessions"
        try:
            names = sorted(os.listdir(sessions))
        except OSError:
            continue
        candidates.extend(
            sessions / name / session_id
            for name in names
            if not name.startswith(".") and _SESSION_ID.fullmatch(name)
        )
    return candidates


def locate_wire(homes: list[Path], session_id: str) -> Path | None:
    """The main-agent journal of one ACP session, or None until it exists."""
    for session_dir in session_dirs(homes, session_id):
        candidate = session_dir.joinpath(*_WIRE_PARTS)
        if candidate.is_file():
            return candidate
    return None


class WireTail:
    """Incremental complete-line reader; ``read`` never raises on bad lines."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.identity: tuple[int, int] | None = None

    @classmethod
    def from_baseline(cls, baseline: WireBaseline) -> WireTail:
        tail = cls(baseline.path)
        tail.identity, tail.offset = baseline.identity, baseline.offset
        return tail

    def _open(self) -> tuple[int, os.stat_result]:
        # SECURITY-PATH-002 shared primitive: non-blocking, final component
        # not followed, single-link regular files only (NotRegularFileError,
        # a ValueError, otherwise).
        with directory(self.path.parent) as parent:
            descriptor = open_regular_at(parent, self.path.name)
        try:
            return descriptor, os.fstat(descriptor)
        except OSError:
            os.close(descriptor)
            raise

    def baseline(self) -> None:
        """Pin identity and current end without reading content (stat only)."""
        descriptor, info = self._open()
        os.close(descriptor)
        self.identity, self.offset = (info.st_dev, info.st_ino), info.st_size

    def read(self) -> list[dict[str, Any]]:
        descriptor, info = self._open()
        try:
            identity = (info.st_dev, info.st_ino)
            if self.identity is None:
                # Never baselined: this runtime's own process created the
                # journal, so everything in it is ours — read from the start.
                self.identity, self.offset = identity, 0
            elif identity != self.identity or info.st_size < self.offset:
                # Replacement or truncation: re-baseline at the new end.
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

"""Pin a resumed Kimi Code wire journal's end while no agent process can write it (#938).

Sibling of background_baseline.py: resume.py captures this after the previous
agent process is reaped and before the new one is spawned, and the unprompted
turn watcher continues from it only when session/load restored that very
session (unprompted_turns.start_unprompted_watcher).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.app.studio_chat.kimi_wire import WireTail, kimi_code_homes, locate_wire

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WireBaseline:
    """Journal identity and end offset; ``identity`` is None when the journal
    existed but could not be stat-ed (the watcher then baselines at first
    sight instead — a possible miss, never a replay)."""

    acp_session_id: str
    path: Path
    identity: tuple[int, int] | None
    offset: int


def usable_baseline(baseline: Any, acp_session_id: str, path: Path | None) -> bool:
    """Only the very journal session/load restored may continue from it."""
    return (
        isinstance(baseline, WireBaseline)
        and baseline.acp_session_id == acp_session_id
        and baseline.identity is not None
        and baseline.path == path
    )


def capture_wire_baseline(cwd: str, acp_session_id: str | None) -> WireBaseline | None:
    """Pre-spawn baseline of a session's journal (None when there is none)."""
    if not acp_session_id:
        return None
    path = locate_wire(kimi_code_homes(cwd), acp_session_id)
    if path is None:
        return None
    tail = WireTail(path)
    try:
        tail.baseline()
    except (OSError, ValueError):
        logger.warning("Kimi wire journal baseline failed for %s", acp_session_id, exc_info=True)
        return WireBaseline(acp_session_id, path, None, 0)
    return WireBaseline(acp_session_id, path, tail.identity, tail.offset)

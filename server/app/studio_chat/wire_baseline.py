"""Locate Kimi journals and pin a resumed journal's end before spawn (#938).

The same launch-aware locator serves resume baselines and the live watcher.
``WireBaseline`` / ``usable_baseline`` live in compaction_wire.py with the
bounded wire-record parser.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from server.app.studio_chat.compaction_wire import WireBaseline
from server.app.studio_chat.kimi_task_store import share_session_dir
from server.app.studio_chat.kimi_wire import WireTail, kimi_code_homes, locate_wire

logger = logging.getLogger(__name__)


def locate_session_wire(
    cwd: str,
    session_id: str,
    *,
    command: str = "kimi",
    args: tuple[str, ...] | list[str] = ("acp",),
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """Select one journal from the frozen launch contract, never from mtime.

    Standard ``kimi acp`` uses the default home because the SDK strips the
    backend override. A non-standard wrapper command uses that same override.
    Explicit handle env is direct writer evidence. A modified kimi argv with
    an override is unproven: two simultaneous roots are neutral, not a guess.
    """
    default = share_session_dir(Path.home() / ".kimi", cwd, session_id)
    if default is None:
        return None
    roots = [default]
    raw = os.environ.get("KIMI_SHARE_DIR")
    mode = "default"
    if env and env.get("KIMI_SHARE_DIR"):
        raw, mode = env["KIMI_SHARE_DIR"], "override"
    elif raw and not (Path(command).name == "kimi" and list(args) == ["acp"]):
        mode = "unknown" if Path(command).name == "kimi" else "override"
    override = (share_session_dir(Path(raw), cwd, session_id) or default) if raw else default
    if mode == "override":
        roots.insert(0, override)
    elif override != default:
        roots.append(override)
    candidates = [root / "wire.jsonl" for root in roots]
    found = [candidate for candidate in candidates if candidate.is_file()]
    if len(found) > 1 and mode == "unknown":
        return None
    return found[0] if found else locate_wire(kimi_code_homes(cwd), session_id)


def capture_wire_baseline(
    cwd: str,
    acp_session_id: str | None,
    *,
    command: str = "kimi",
    args: tuple[str, ...] | list[str] = ("acp",),
    env: Mapping[str, str] | None = None,
) -> WireBaseline | None:
    """Pre-spawn baseline of a session's journal (None when there is none)."""
    if not acp_session_id:
        return None
    if (
        path := locate_session_wire(cwd, acp_session_id, command=command, args=args, env=env)
    ) is None:
        return None
    tail = WireTail(path)
    try:
        tail.baseline()
    except (OSError, ValueError):
        logger.warning("Kimi wire journal baseline failed for %s", acp_session_id, exc_info=True)
        return WireBaseline(acp_session_id, path, None, 0)
    return WireBaseline(acp_session_id, path, tail.identity, tail.offset)

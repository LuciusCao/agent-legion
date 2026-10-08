"""Auto-approval scope for local read-only Studio chat tool calls (#921).

The ACP ``kind`` / ``locations`` / ``rawInput`` of a permission request are
self-reported by the agent, so a read-class kind alone is not a safe reason
to skip the human: the agent runs as the server user. The minimal
auto-approvable set is therefore:

* kind is ``read`` or ``search`` (the Read/Glob/Grep class), AND
* every declared target path resolves (symlinks followed) inside the
  session workspace's MCP staging directory
  (``data/studio-mcp-files/<workspace_id>``) — the only local files the
  platform itself hands the agent (MCP read/write tools stage there), AND
* no other rawInput string escapes it (absolute / home-relative / ``..``),
  AND the request offers a one-shot ``allow_once`` option (an
  ``allow_always`` answer could let the agent stop asking for the kind).

Anything else — no declared path, a path outside staging, a path string
that cannot be resolved at all, an unknown shape — takes the human path; a false negative only costs a click.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from server.app.mcp_server.local_files import staging_root

# ACP ToolKind values that are local and read-only (the Read/Glob/Grep class).
READ_ONLY_TOOL_KINDS = frozenset({"read", "search"})

# rawInput keys that name the target file/dir of a read-class tool.
_PATH_KEYS = frozenset(
    {"path", "file_path", "filePath", "absolute_path", "directory", "dir", "cwd", "root"}
)


def _within(candidate: str, root: str) -> bool:
    try:
        return os.path.commonpath([candidate, root]) == root
    except ValueError:
        return False


def _resolve(raw: str, base: str) -> str:
    return os.path.realpath(os.path.join(base, os.path.expanduser(raw)))


def _escapes(raw: str) -> bool:
    return raw.startswith(("/", "~")) or ".." in Path(raw).parts


def _declared_paths(tool_call: dict[str, Any]) -> tuple[list[str], list[str]] | None:
    """(target paths, other rawInput strings); None on a malformed shape."""
    targets: list[str] = []
    for location in tool_call.get("locations") or []:
        if not isinstance(location, dict) or not isinstance(location.get("path"), str):
            return None
        targets.append(location["path"])
    raw_input = tool_call.get("rawInput")
    others: list[str] = []
    if isinstance(raw_input, dict):
        for key, value in raw_input.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                # Nested containers are an unknown shape: they could carry
                # targets this check never sees, so take the human path.
                if isinstance(item, (dict, list)):
                    return None
                if isinstance(item, str):
                    (targets if key in _PATH_KEYS else others).append(item)
    elif raw_input is not None:
        return None
    return targets, others


def is_staging_read_only_tool_call(
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    workspace_id: str,
    cwd: str,
) -> bool:
    """Whether a read-class call stays inside the workspace staging dir."""
    if str(tool_call.get("kind") or "") not in READ_ONLY_TOOL_KINDS:
        return False
    if not any(option.get("kind") == "allow_once" for option in options):
        return False
    declared = _declared_paths(tool_call)
    if declared is None:
        return False
    targets, others = declared
    if not targets:
        return False
    try:
        root = os.path.realpath(staging_root(workspace_id))
    except ValueError:
        return False
    try:
        resolved = [_resolve(target, cwd) for target in targets]
    except (ValueError, OSError):
        # #984: an unresolvable path string (embedded NUL, OS-level refusal
        # such as an over-long name) fails closed to the human path instead
        # of erroring the permission RPC.
        return False
    if not all(_within(candidate, root) for candidate in resolved):
        return False
    return not any(_escapes(value) for value in others)


def normalize_selected_option(options: list[dict[str, Any]], option_id: str | None) -> str | None:
    """Validate a chosen option against the offered set; downgrade
    ``allow_always`` to the offered ``allow_once`` so every later tool call
    (and every terminal it spawns) comes back through a permission request.
    Returns None (treated as a denial) when the id was not offered, or when
    ``allow_always`` was chosen but no one-shot option exists to narrow to."""
    chosen = next((o for o in options if o.get("optionId") == option_id), None)
    if chosen is None:
        return None
    if chosen.get("kind") == "allow_always":
        once = next((o for o in options if o.get("kind") == "allow_once"), None)
        return None if once is None else str(once["optionId"])
    return str(chosen["optionId"])


def is_allow_option(options: list[dict[str, Any]], option_id: str) -> bool:
    return any(
        o.get("optionId") == option_id and str(o.get("kind") or "").startswith("allow")
        for o in options
    )

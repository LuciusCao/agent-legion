"""One-shot terminal grants tying ACP ``terminal/create`` to approved
permission requests in Studio chat (#921; env/cwd fences: terminal_policy.py).

Each terminal consumes one grant minted by a permission request the human
answered (or the session allow-all switch approved). Platform
auto-approvals (agent-legion MCP tools, staged read-only calls) never mint
grants. Grants are one-shot and short-lived; when the approved call
declared a command, the terminal must run exactly that command (as the
whole command line, or as the ``-c`` script). A shell-side ``cd <dir> &&``
wrapper is not accepted for a bound grant: its directory would be resolved
by the shell at exec time, outside the pinned terminal cwd. A permission payload that carries no command
(kimi's approval bridge sends only the tool name) mints an unbound grant:
still one approved request per terminal, but not command-bound (#954).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

# Grant lifetime: the agent spawns right after the answer; a stale grant must
# not linger for a later, unapproved command.
GRANT_TTL_SECONDS = 300
MAX_PENDING_GRANTS = 16

# Decisions made by the platform itself (no human in the loop): never a
# basis for running a terminal command.
AUTO_DECISIONS = frozenset({"auto_approved", "auto_read_only"})


@dataclass
class _Grant:
    command: str | None
    expires_at: float


class TerminalGrants:
    """One-shot terminal grants minted by approved permission requests.

    Owned by the session loop (request_permission and terminal/create both
    run on it), so no locking is needed.
    """

    def __init__(self) -> None:
        self._grants: list[_Grant] = []

    def grant(self, tool_call: dict[str, Any]) -> None:
        raw_input = tool_call.get("rawInput")
        command = raw_input.get("command") if isinstance(raw_input, dict) else None
        self._prune()
        self._grants.append(
            _Grant(
                command=command.strip() if isinstance(command, str) and command.strip() else None,
                expires_at=time.monotonic() + GRANT_TTL_SECONDS,
            )
        )
        del self._grants[:-MAX_PENDING_GRANTS]

    def consume(self, command: str, args: list[str] | None) -> bool:
        """Take the grant matching this command line; False when none does."""
        self._prune()
        bound = [
            g for g in self._grants if g.command is not None and _runs(g.command, command, args)
        ]
        unbound = [g for g in self._grants if g.command is None]
        candidates = bound or unbound
        if not candidates:
            return False
        self._grants.remove(candidates[0])
        return True

    def _prune(self) -> None:
        now = time.monotonic()
        self._grants = [g for g in self._grants if g.expires_at > now]


def _runs(approved: str, command: str, args: list[str] | None) -> bool:
    """Exact match of the approved command against the terminal request."""
    argv = [command, *(args or [])]
    if " ".join(argv).strip() == approved:
        return True
    return len(argv) == 3 and argv[1] == "-c" and argv[2].strip() == approved

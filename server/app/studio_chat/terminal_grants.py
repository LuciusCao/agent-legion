"""One-shot terminal grants tying ACP ``terminal/create`` to approved
permission requests in Studio chat (#921; env/cwd fences: terminal_policy.py).

Each terminal consumes one grant minted by a permission request the human
answered (or the session allow-all switch approved). Platform
auto-approvals (agent-legion MCP tools, staged read-only calls) never mint
grants. Grants are one-shot and short-lived; when the approved call
declared a command, the terminal must run exactly that command (as the
whole command line, or as the ``-c`` script, allowing only a leading
single-quoted ``cd '<dir>' && `` wrapper whose ``<dir>`` must also stay
inside the session root). A permission payload that carries no command
(kimi's approval bridge sends only the tool name) mints an unbound grant:
still one approved request per terminal, but not command-bound (#954).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from acp import RequestError

from server.app.studio_chat.terminal_policy import confined_cwd

# Grant lifetime: the agent spawns right after the answer; a stale grant must
# not linger for a later, unapproved command.
GRANT_TTL_SECONDS = 300
MAX_PENDING_GRANTS = 16

# The cd wrapper a shell-based agent may put in front of the approved
# command (kimi: ``cd <shellQuote(cwd)> && <command>``).
_CD_WRAPPER = re.compile(r"cd '((?:[^']|'\\'')*)' && ")

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

    def consume(self, command: str, args: list[str] | None, *, root: str) -> bool:
        """Take the grant matching this command line; False when none does."""
        self._prune()
        bound = [
            g
            for g in self._grants
            if g.command is not None and _runs(g.command, command, args, root)
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


def _runs(approved: str, command: str, args: list[str] | None, root: str) -> bool:
    """Exact match of the approved command against the terminal request;
    a cd wrapper's target must itself stay inside the session root."""
    argv = [command, *(args or [])]
    if " ".join(argv).strip() == approved:
        return True
    if len(argv) != 3 or argv[1] != "-c":
        return False
    script = argv[2].strip()
    if script == approved:
        return True
    wrapper = _CD_WRAPPER.match(script)
    if wrapper is None or script[wrapper.end() :] != approved:
        return False
    try:
        confined_cwd(wrapper.group(1).replace("'\\''", "'"), root)
    except RequestError:
        return False
    return True

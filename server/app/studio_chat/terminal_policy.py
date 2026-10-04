"""Admission policy for ACP ``terminal/create`` in Studio chat (#921).

Three independent fences, applied before any subprocess is spawned:

* **Environment allowlist** — a terminal child never inherits the server
  process environment (which carries deployment secrets loaded from
  ``.env``). It gets the same minimal base the ACP SDK uses for the agent
  itself plus locale/tempdir basics (the ``shared/code_sandbox.child_env``
  idea), then the agent's own literal overrides on top.
* **Working directory confinement** — the requested cwd must resolve
  (symlinks followed) inside the session's working directory.
* **Permission linkage** — each terminal consumes one grant minted by a
  permission request the human answered (or the session allow-all switch
  approved). Platform auto-approvals (agent-legion MCP tools, staged
  read-only calls) never mint grants. Grants are one-shot and short-lived;
  when the approved call declared a command, the terminal must run exactly
  that command (as the whole command line, or as the ``-c`` script, allowing
  only a leading single-quoted ``cd '<dir>' && `` wrapper). A permission
  payload that carries no command (kimi's approval bridge sends only the
  tool name) mints an unbound grant: still one approved request per
  terminal, but not command-bound.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from acp import RequestError

# acp.transports.DEFAULT_INHERITED_ENV_VARS (what the SDK keeps for the agent
# subprocess) plus TMPDIR; LANG/LC_* are added by prefix below.
BASE_ENV_KEYS = ("HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER", "TMPDIR")

# Grant lifetime: the agent spawns right after the answer; a stale grant must
# not linger for a later, unapproved command.
GRANT_TTL_SECONDS = 300
MAX_PENDING_GRANTS = 16

# The cd wrapper a shell-based agent may put in front of the approved
# command (kimi: ``cd <shellQuote(cwd)> && <command>``).
_CD_WRAPPER = re.compile(r"cd '(?:[^']|'\\'')*' && ")

# Decisions made by the platform itself (no human in the loop): never a
# basis for running a terminal command.
AUTO_DECISIONS = frozenset({"auto_approved", "auto_read_only"})


def terminal_env(overrides: Iterable[Any] | None) -> dict[str, str]:
    """Allowlisted base environment plus the agent's literal overrides."""
    env: dict[str, str] = {}
    for key in BASE_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None and not value.startswith("()"):
            env[key] = value
    for key, value in os.environ.items():
        if key == "LANG" or key.startswith("LC_"):
            env[key] = value
    for item in overrides or []:
        env[str(item.name)] = str(item.value)
    return env


def confined_cwd(requested: str | None, root: str) -> str:
    """Resolve the terminal cwd; refuse anything outside the session root."""
    real_root = os.path.realpath(root)
    if not requested:
        return real_root
    resolved = os.path.realpath(os.path.join(real_root, os.path.expanduser(requested)))
    try:
        inside = os.path.commonpath([resolved, real_root]) == real_root
    except ValueError:
        inside = False
    if not inside:
        raise RequestError.invalid_params({"reason": "terminal cwd outside the session root"})
    return resolved


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
    if len(argv) != 3 or argv[1] != "-c":
        return False
    script = argv[2].strip()
    if script == approved:
        return True
    wrapper = _CD_WRAPPER.match(script)
    return wrapper is not None and script[wrapper.end() :] == approved

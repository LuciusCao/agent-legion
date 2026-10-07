"""One-shot terminal grants tying ACP ``terminal/create`` to approved
permission requests in Studio chat (#921; env/cwd fences: terminal_policy.py).

Each terminal consumes one grant minted by a permission request the human
answered (or the session allow-all switch approved). Platform
auto-approvals (agent-legion MCP tools, staged read-only calls) never mint
grants. Grants are one-shot and short-lived; when the approved call
declared a command, the terminal must run exactly that command (as the
whole command line, or as the ``-c`` script). A shell-side ``cd <dir> &&``
wrapper is not accepted for a bound grant: its directory would be resolved
by the shell at exec time, outside the pinned terminal cwd (#1086).

Command binding (#954, sources and the kimi event order they rely on:
tool_call_commands.py): a grant binds the command declared by the request
itself or, failing that, by the same ``toolCallId`` on ``session/update``
before the answer (shown on the card); a grant still unbound at approval
late-binds to that call's first ``rawInput.command`` (first write wins).
A grant whose call was seen on ``session/update`` yet is still unbound when
the terminal is created is refused — kimi always announces the command
before spawning. A grant for a call never seen there (kimi subagents) stays
unbound: one approved request per terminal, not command-bound — and is only
spendable while no announced or bound grant is in flight, so a main-agent
call cannot swap its approved command by borrowing a subagent's grant; and
since a terminal is not attributable to a call, a bound grant taken by
command match revokes the unbound grants in flight (a subagent running the
bound command first must not leave the bound call an unbound grant).
Residual boundary (inherent to unannounced subagent calls, pre-#954): an
unbound grant is one terminal of any command — while every grant in flight
is unbound (including subagent grants approved after a bound grant was
consumed or expired) the oldest is spendable by any terminal; the invariant
kept is approvals >= terminals. A command-less approval of a kind that
never spawns a terminal mints none.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from server.app.studio_chat.tool_call_args import call_id, declared_command
from server.app.studio_chat.tool_call_commands import ToolCallCommands

# Grant lifetime: the agent spawns right after the answer; a stale grant must
# not linger for a later, unapproved command.
GRANT_TTL_SECONDS = 300
MAX_PENDING_GRANTS = 16

# Decisions made by the platform itself (no human in the loop): never a
# basis for running a terminal command.
AUTO_DECISIONS = frozenset({"auto_approved", "auto_read_only"})


@dataclass
class _Grant:
    tool_call_id: str | None
    command: str | None
    expires_at: float


class TerminalGrants:
    """One-shot terminal grants minted by approved permission requests.

    Owned by the session loop (request_permission and terminal/create both
    run on it), so no locking is needed.
    """

    def __init__(self) -> None:
        self._grants: list[_Grant] = []
        # Fed by session/update; calls holding a live grant stay observed.
        self.calls = ToolCallCommands(pinned=self._granted_calls)

    def observe(self, update: dict[str, Any]) -> None:
        """Record a session/update; late-bind grants still unbound."""
        command = self.calls.observe(update)
        if command is None:
            return
        for grant in self._grants:
            if grant.command is None and grant.tool_call_id == update.get("toolCallId"):
                grant.command = command

    def begin(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """Bind a permission request before it is shown; pair with ``end``."""
        self.calls.hold(call_id(tool_call), True)
        return self.calls.bind(tool_call)

    def end(self, tool_call: dict[str, Any]) -> None:
        self.calls.hold(call_id(tool_call), False)

    def grant(self, tool_call: dict[str, Any]) -> None:
        """Mint for an approved request (the payload returned by ``begin``)."""
        if not self.calls.may_spawn_terminal(tool_call):
            return
        tool_call_id = call_id(tool_call)
        self._prune()
        self._grants.append(
            _Grant(
                tool_call_id=tool_call_id,
                command=declared_command(tool_call) or self.calls.started_command(tool_call_id),
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
        if bound:
            self._grants.remove(bound[0])
            # A terminal is not attributable to a call: with never-announced
            # unbound grants in flight, a subagent running the same command
            # may have taken this bound grant, and the bound call's swapped
            # command would then find only unbound grants. Revoke them so
            # the fallback fails closed (cost: a racing subagent Bash is
            # refused; the agent retries under a fresh approval).
            self._grants = [g for g in self._grants if not self._unannounced(g)]
            return True
        # While any announced/bound grant is in flight, a non-matching command
        # fails closed instead of spending an unrelated subagent's unbound
        # grant (a main call swapping its approved command would otherwise
        # borrow it). Cost: a subagent Bash racing a main-agent Bash may be
        # refused (explicit error; the agent retries).
        if not self._grants or not all(self._unannounced(g) for g in self._grants):
            return False
        self._grants.pop(0)
        return True

    def _unannounced(self, grant: _Grant) -> bool:
        return grant.command is None and not self.calls.seen(grant.tool_call_id)

    def _granted_calls(self) -> list[str]:
        return [g.tool_call_id for g in self._grants if g.tool_call_id is not None]

    def _prune(self) -> None:
        now = time.monotonic()
        self._grants = [g for g in self._grants if g.expires_at > now]


def _runs(approved: str, command: str, args: list[str] | None) -> bool:
    """Exact match of the approved command against the terminal request."""
    argv = [command, *(args or [])]
    if " ".join(argv).strip() == approved:
        return True
    return len(argv) == 3 and argv[1] == "-c" and argv[2].strip() == approved

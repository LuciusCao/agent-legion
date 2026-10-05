"""Command binding source for Studio chat ACP terminal grants (#954).

kimi's approval bridge sends ``session/request_permission`` with only the
tool name and a truncated action summary (``Running: <first 50 chars>…``),
never the command itself. The command does reach the client earlier, on the
same ACP ``toolCallId``: kimi emits the ``tool_call`` (or the
``tool_call_update`` upgrading a lazily streamed one) from
``tool.call.started`` carrying ``rawInput`` = the tool args, and its engine
only raises the approval after that event. Its Bash tool then spawns
``<shell> -c "cd '<cwd>' && <rawInput.command>"`` through ``terminal/create``.

This module remembers the latest ``rawInput.command`` and ``kind`` each
tool call declared, so the permission request can be bound to that command
(terminal_grants.py then requires an exact match) and the card shown to the
human carries the same command the grant binds — one source for both. When
no command was declared anywhere the grant stays unbound, as before: failing
closed there would take the Bash tool away from supported agents.

Notifications and requests are dispatched as tasks in arrival order and both
handlers reach this state before their first real await, so a ``tool_call``
sent before the permission request is always observed first.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

# Bounded: only the calls still awaiting an answer matter.
MAX_OBSERVED_CALLS = 64

_TOOL_CALL_UPDATES = frozenset({"tool_call", "tool_call_update"})

# ACP tool kinds that never spawn a terminal: approving one of them without a
# declared command mints no grant, so an approved Edit/WebFetch can no longer
# be spent on an arbitrary terminal command. ``execute``/``other`` (and an
# undeclared kind) keep the unbound grant.
NON_TERMINAL_KINDS = frozenset(
    {"read", "edit", "delete", "move", "search", "fetch", "think", "switch_mode"}
)


def declared_command(tool_call: dict[str, Any]) -> str | None:
    raw_input = tool_call.get("rawInput")
    command = raw_input.get("command") if isinstance(raw_input, dict) else None
    if isinstance(command, str) and command.strip():
        return command.strip()
    return None


@dataclass
class _Observed:
    command: str | None = None
    kind: str | None = None


class ToolCallCommands:
    """Per-session ``toolCallId`` → declared command/kind, fed by
    ``session/update``; owned by the session loop like TerminalGrants."""

    def __init__(self) -> None:
        self._calls: OrderedDict[str, _Observed] = OrderedDict()

    def observe(self, update: dict[str, Any]) -> None:
        if update.get("sessionUpdate") not in _TOOL_CALL_UPDATES:
            return
        tool_call_id = update.get("toolCallId")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            return
        entry = self._calls.pop(tool_call_id, None) or _Observed()
        kind = update.get("kind")
        if isinstance(kind, str) and kind:
            entry.kind = kind
        command = declared_command(update)
        if command is not None:
            entry.command = command
        self._calls[tool_call_id] = entry
        while len(self._calls) > MAX_OBSERVED_CALLS:
            self._calls.popitem(last=False)

    def bind(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """The permission payload carrying the command its grant binds to.

        A command declared by the request itself wins; otherwise the one the
        same tool call declared on ``session/update`` is folded into
        ``rawInput`` — the persisted card renders it, and the grant reads it.
        """
        entry = self._entry(tool_call)
        if declared_command(tool_call) is not None or entry is None or entry.command is None:
            return tool_call
        raw_input = tool_call.get("rawInput")
        base = raw_input if isinstance(raw_input, dict) else {}
        return {**tool_call, "rawInput": {**base, "command": entry.command}}

    def may_spawn_terminal(self, tool_call: dict[str, Any]) -> bool:
        """False for a command-less call whose kind never spawns a terminal."""
        if declared_command(tool_call) is not None:
            return True
        kind = tool_call.get("kind")
        if not isinstance(kind, str) or not kind:
            entry = self._entry(tool_call)
            kind = entry.kind if entry is not None else None
        return kind not in NON_TERMINAL_KINDS

    def _entry(self, tool_call: dict[str, Any]) -> _Observed | None:
        tool_call_id = tool_call.get("toolCallId")
        return self._calls.get(tool_call_id) if isinstance(tool_call_id, str) else None

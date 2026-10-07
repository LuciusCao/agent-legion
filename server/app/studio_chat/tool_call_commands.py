"""Command binding sources for Studio chat ACP terminal grants (#954).

kimi (0.43, checked against the shipped bundle) asks
``session/request_permission`` with only ``toolCallId``/``title`` and a
summary truncated to 50 characters (``Running: <command>…``) — no ``kind``,
no ``rawInput``. Its ``toolExecutorService.prepareToolCall`` runs the
approval (``fireBeforeExecute``) BEFORE ``dispatchToolCall`` emits
``tool.call.started``, the only event whose ``tool_call``/``tool_call_update``
carries ``rawInput``; the Bash spawn (``terminal/create`` of
``<shell> -c "cd '<cwd>' && <args.command>"``) runs after that, in the
executor batch. Two sources follow from that order, both keyed by the ACP
``toolCallId`` (``<turnId>:<id>``) the permission request shares:

* before the answer — the streamed-args path: the lazy ``tool_call``
  (``kind`` = ``execute`` for Bash) and every ``tool_call_update`` delta
  carry the *cumulative* argument JSON as their text content. Once that text
  parses as a complete JSON object with a string ``command`` the stream is
  over (the approval only starts after the model finished the call); that
  command is folded into the permission payload, so the card shows it and
  the grant binds it;
* after the answer — the first ``rawInput.command`` (``tool.call.started``)
  late-binds a grant still unbound at approval time; first write wins, later
  updates cannot re-point it. ``tool.call.started`` is emitted synchronously
  on dispatch, before the executor reaches the spawn, so it precedes
  ``terminal/create`` on the wire (kimi's own terminal/card correlation,
  ``bashCallsAwaitingTerminal``, relies on the same order).

kimi only forwards the MAIN agent's tool events over ACP (``driverFor``);
a subagent's Bash approval and terminal arrive with no notification at all
for its ``toolCallId``. A call never seen on ``session/update`` therefore
keeps the unbound one-shot grant — failing closed there would take Bash away
from subagents — while a seen call must be bound by the time its terminal
is created (terminal_grants.py).
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from server.app.studio_chat.tool_call_args import declared_command, streamed_command

# Bounded LRU; calls awaiting an answer or holding a live grant are pinned.
MAX_OBSERVED_CALLS = 64

_TOOL_CALL_UPDATES = frozenset({"tool_call", "tool_call_update"})
_FINISHED = frozenset({"completed", "failed"})

# ACP tool kinds that never spawn a terminal: approving one of them without a
# declared command mints no grant, so an approved Edit/WebFetch can no longer
# be spent on an arbitrary terminal command. ``execute``/``other`` (and an
# undeclared kind) keep the grant.
NON_TERMINAL_KINDS = frozenset(
    {"read", "edit", "delete", "move", "search", "fetch", "think", "switch_mode"}
)


@dataclass
class _Observed:
    command: str | None = None  # latest command visible before the answer
    started: str | None = None  # first rawInput.command (late-bind source)
    kind: str | None = None


class ToolCallCommands:
    """Per-session ``toolCallId`` → declared command/kind, fed by
    ``session/update``; owned by the session loop like TerminalGrants."""

    def __init__(self, pinned: Callable[[], Iterable[str]] = tuple) -> None:
        self._calls: OrderedDict[str, _Observed] = OrderedDict()
        self._pinned = pinned
        self._awaiting: Counter[str] = Counter()

    def observe(self, update: dict[str, Any]) -> str | None:
        """Record one update; returns its ``rawInput.command`` if any."""
        tool_call_id = update.get("toolCallId")
        if update.get("sessionUpdate") not in _TOOL_CALL_UPDATES or not isinstance(
            tool_call_id, str
        ):
            return None
        entry = self._calls.pop(tool_call_id, None) or _Observed()
        self._calls[tool_call_id] = entry
        if isinstance(kind := update.get("kind"), str) and kind:
            entry.kind = kind
        raw = declared_command(update)
        if raw is not None:
            entry.command = raw
            entry.started = entry.started or raw
        elif entry.kind == "execute" and update.get("status") not in _FINISHED:
            entry.command = streamed_command(update) or entry.command
        self._evict()
        return raw

    def seen(self, tool_call_id: str | None) -> bool:
        return tool_call_id is not None and tool_call_id in self._calls

    def started_command(self, tool_call_id: str | None) -> str | None:
        entry = self._calls.get(tool_call_id) if tool_call_id is not None else None
        return entry.started if entry is not None else None

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

    def hold(self, tool_call_id: str | None, held: bool) -> None:
        """Pin a call while its permission request awaits the answer."""
        if tool_call_id is None:
            return
        self._awaiting[tool_call_id] += 1 if held else -1
        self._awaiting = +self._awaiting

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

    def _evict(self) -> None:
        excess = len(self._calls) - MAX_OBSERVED_CALLS
        if excess <= 0:
            return
        pinned = set(self._awaiting) | set(self._pinned())
        for tool_call_id in [key for key in self._calls if key not in pinned][:excess]:
            del self._calls[tool_call_id]

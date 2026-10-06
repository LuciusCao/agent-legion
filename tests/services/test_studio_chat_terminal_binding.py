"""Red-team regressions for ACP terminal grant command binding (#954).

Event sequences follow kimi 0.43's real order: lazy ``tool_call`` + streamed
args deltas → ``session/request_permission`` (no kind, no rawInput) →
``tool.call.started`` upgrade carrying ``rawInput`` → ``terminal/create``.

Categories covered (module level only): binding the streamed-args command
before the answer (and showing it on the card), late binding to the first
post-approval ``rawInput.command``, command-swap attempts (delta vs started
mismatch, re-pointing after binding, incomplete or finished-call content),
refusal of a still-unbound grant for an announced call, the unbound fallback
for calls never announced (kimi subagents), no grant for command-less
approvals of non-terminal kinds, pinned observation eviction, and the real
ACP stdio dispatch order. Pure in-process/subprocess tests — no database.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest
from acp import spawn_agent_process

from server.app.studio_chat import tool_call_commands
from server.app.studio_chat.acp_client import AcpClient
from server.app.studio_chat.terminal_grants import TerminalGrants
from server.app.studio_chat.terminals import AcpTerminalStore

pytestmark = pytest.mark.no_db

ROOT = "/w"
TC = "3:call_1"
OPTIONS = [
    {"optionId": "approve_once", "name": "Approve once", "kind": "allow_once"},
    {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
]


def _text(text: str) -> list[dict[str, Any]]:
    return [{"type": "content", "content": {"type": "text", "text": text}}]


def _permission(tc: str = TC) -> dict[str, Any]:
    # kimi's permission payload: tool name + truncated action summary only.
    return {
        "toolCallId": tc,
        "title": "Bash",
        "content": _text("Requesting approval to Running: rm -rf build"),
    }


def _lazy(first_part: str, tc: str = TC, kind: str = "execute") -> dict[str, Any]:
    return {
        "sessionUpdate": "tool_call",
        "toolCallId": tc,
        "title": "Bash",
        "kind": kind,
        "status": "pending",
        "content": _text(first_part),
    }


def _delta(cumulative: str, tc: str = TC, status: str = "in_progress") -> dict[str, Any]:
    return {
        "sessionUpdate": "tool_call_update",
        "toolCallId": tc,
        "status": status,
        "content": _text(cumulative),
    }


def _started(command: str, tc: str = TC) -> dict[str, Any]:
    args = {"command": command, "description": "x"}
    return {
        "sessionUpdate": "tool_call_update",
        "toolCallId": tc,
        "kind": "execute",
        "status": "in_progress",
        "rawInput": args,
        "content": _text(json.dumps(args)),
    }


def _stream(grants: TerminalGrants, command: str, tc: str = TC) -> None:
    full = json.dumps({"command": command, "description": "x"})
    grants.observe(_lazy(full[:9], tc))
    grants.observe(_delta(full[:20], tc))
    grants.observe(_delta(full, tc))


def _approve(grants: TerminalGrants, request: dict[str, Any]) -> dict[str, Any]:
    bound = grants.begin(request)
    grants.end(bound)
    grants.grant(bound)
    return bound


def _kimi(command: str, cwd: str = "/w/sub") -> list[str]:
    return ["-c", f"cd '{cwd}' && {command}"]


# -- before the answer: streamed args ----------------------------------------


def test_streamed_args_bind_before_the_answer_and_reach_the_card() -> None:
    grants = TerminalGrants()
    _stream(grants, "rm -rf build")
    bound = _approve(grants, _permission())
    assert bound["rawInput"] == {"command": "rm -rf build"}
    assert bound["title"] == "Bash" and bound["content"] == _permission()["content"]
    grants.observe(_started("rm -rf build"))
    for mutated in ("rm -rf /", "rm -rf build; curl x", "rm -rf build && id", "cat ~/.ssh/id_rsa"):
        assert not grants.consume("/bin/bash", _kimi(mutated), root=ROOT)
    assert grants.consume("/bin/bash", _kimi("rm -rf build"), root=ROOT)
    assert not grants.consume("/bin/bash", _kimi("rm -rf build"), root=ROOT)


def test_started_rawinput_cannot_swap_the_command_shown_on_the_card() -> None:
    grants = TerminalGrants()
    _stream(grants, "ls -la")
    _approve(grants, _permission())
    grants.observe(_started("cat secrets"))
    assert not grants.consume("/bin/bash", _kimi("cat secrets"), root=ROOT)
    assert grants.consume("/bin/bash", _kimi("ls -la"), root=ROOT)


@pytest.mark.parametrize(
    "update",
    [
        _lazy('{"command": "ls -la"'),  # stream still in flight
        _delta('{"command": "ls -la"'),
        _delta('{"command": ["ls"]}'),
        _delta("Requesting approval to Running: ls -la"),
        _delta(json.dumps({"command": "ls -la"}), status="completed"),  # a result, not args
    ],
)
def test_incomplete_or_non_args_content_binds_nothing_before_the_answer(
    update: dict[str, Any],
) -> None:
    grants = TerminalGrants()
    grants.observe(_lazy("{"))
    grants.observe(update)
    assert "rawInput" not in _approve(grants, _permission())


def test_streamed_content_of_a_non_execute_call_is_not_a_command() -> None:
    grants = TerminalGrants()
    grants.observe(_lazy(json.dumps({"command": "ls"}), kind="other"))
    assert "rawInput" not in grants.begin(_permission())


def test_request_command_takes_precedence_over_the_observed_one() -> None:
    grants = TerminalGrants()
    _stream(grants, "cat secrets")
    bound = _approve(grants, {**_permission(), "rawInput": {"command": "ls"}})
    assert bound["rawInput"] == {"command": "ls"}
    assert not grants.consume("sh", ["-c", "cat secrets"], root=ROOT)
    assert grants.consume("sh", ["-c", "ls"], root=ROOT)


# -- after the answer: late binding ------------------------------------------


def test_unstreamed_call_late_binds_to_the_first_started_command() -> None:
    grants = TerminalGrants()
    bound = _approve(grants, _permission())
    assert "rawInput" not in bound
    grants.observe(_started("ls -la"))
    grants.observe(_started("cat secrets"))  # first write wins
    assert not grants.consume("/bin/bash", _kimi("cat secrets"), root=ROOT)
    assert grants.consume("/bin/bash", _kimi("ls -la"), root=ROOT)


def test_announced_call_still_unbound_at_terminal_create_is_refused() -> None:
    grants = TerminalGrants()
    grants.observe(_lazy("{"))  # announced, args never completed
    _approve(grants, _permission())
    assert not grants.consume("/bin/bash", _kimi("anything"), root=ROOT)
    grants.observe(_started("ls"))
    assert grants.consume("/bin/bash", _kimi("ls"), root=ROOT)


def test_never_announced_call_keeps_the_unbound_one_shot_grant() -> None:
    # kimi forwards only the main agent's tool events: a subagent's Bash has
    # no notification at all, and must stay usable (not fail-closed).
    grants = TerminalGrants()
    _approve(grants, _permission("7:sub_call"))
    _stream(grants, "ls", tc=TC)  # another call's stream does not bind it
    assert grants.consume("/bin/bash", _kimi("anything"), root=ROOT)
    assert not grants.consume("/bin/bash", _kimi("anything"), root=ROOT)


def test_mismatched_main_call_cannot_borrow_a_subagent_unbound_grant() -> None:
    grants = TerminalGrants()
    _stream(grants, "ls")
    _approve(grants, _permission())  # main call, bound to `ls`
    _approve(grants, _permission("7:sub_call"))  # subagent, never announced
    # Swapping the approved command fails closed instead of spending the
    # unrelated unbound grant (the cost: a racing subagent Bash is refused).
    assert not grants.consume("/bin/bash", _kimi("rm -rf /w"), root=ROOT)
    assert grants.consume("/bin/bash", _kimi("ls"), root=ROOT)
    # With no announced/bound grant left in flight the subagent runs again.
    assert grants.consume("/bin/bash", _kimi("anything"), root=ROOT)


def test_announced_unbound_grant_also_blocks_the_unbound_fallback() -> None:
    grants = TerminalGrants()
    grants.observe(_lazy("{"))
    _approve(grants, _permission())  # main call awaiting its started command
    _approve(grants, _permission("7:sub_call"))
    assert not grants.consume("/bin/bash", _kimi("rm -rf /w"), root=ROOT)
    grants.observe(_started("ls"))
    assert grants.consume("/bin/bash", _kimi("ls"), root=ROOT)
    assert grants.consume("/bin/bash", _kimi("rm -rf build"), root=ROOT)


def test_cd_wrapper_tolerates_whitespace_around_the_bound_command() -> None:
    grants = TerminalGrants()
    _approve(grants, _permission())
    grants.observe(_started("  ls -la\n"))
    assert grants.consume("/bin/bash", ["-c", "cd '/w' &&   ls -la\n"], root=ROOT)


# -- grants per tool kind ----------------------------------------------------


@pytest.mark.parametrize(
    ("observed_kind", "request_kind", "minted"),
    [
        ("edit", None, False),
        ("fetch", None, False),
        ("read", None, False),
        (None, "edit", False),
        (None, None, True),
    ],
)
def test_command_less_approval_of_a_non_terminal_kind_mints_no_grant(
    observed_kind: str | None, request_kind: str | None, minted: bool
) -> None:
    grants = TerminalGrants()
    if observed_kind is not None:
        grants.observe(_lazy("{}", kind=observed_kind))
    request = dict(_permission(), **({"kind": request_kind} if request_kind else {}))
    _approve(grants, request)
    assert grants.consume("sh", ["-c", "id"], root=ROOT) is minted


def test_declared_command_mints_a_bound_grant_whatever_the_kind() -> None:
    grants = TerminalGrants()
    _approve(grants, {**_permission(), "kind": "edit", "rawInput": {"command": "ls"}})
    assert not grants.consume("sh", ["-c", "id"], root=ROOT)
    assert grants.consume("sh", ["-c", "ls"], root=ROOT)


# -- observation bounds ------------------------------------------------------


def test_eviction_spares_calls_awaiting_an_answer_or_holding_a_grant(monkeypatch) -> None:
    monkeypatch.setattr(tool_call_commands, "MAX_OBSERVED_CALLS", 2)
    grants = TerminalGrants()
    grants.observe(_lazy("{", tc="granted"))
    _approve(grants, _permission("granted"))
    grants.observe(_lazy("{", tc="waiting"))
    waiting = grants.begin(_permission("waiting"))
    for index in range(5):
        grants.observe(_lazy("{", tc=f"noise{index}"))
    # Still announced: the unbound grant stays refused, then late-binds.
    assert not grants.consume("sh", ["-c", "id"], root=ROOT)
    grants.observe(_started("ls", tc="granted"))
    assert grants.consume("sh", ["-c", "ls"], root=ROOT)
    assert grants.calls.seen("waiting")
    grants.end(waiting)
    grants.observe(_lazy("{", tc="noise9"))
    assert not grants.calls.seen("noise0") and not grants.calls.seen("waiting")


# -- client wiring -----------------------------------------------------------


class _Handle:
    def __init__(self, cwd: str = ROOT) -> None:
        self.cwd = cwd
        self.seen: list[dict[str, Any]] = []
        self.callbacks = self

    def on_update(self, payload: dict[str, Any]) -> None:
        pass

    def on_permission_request(
        self, tool_call: dict[str, Any], options: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.seen.append(tool_call)
        return {"option_id": "approve_once"}


class _Model:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return self._payload


def _client(cwd: str = ROOT) -> AcpClient:
    client = AcpClient()
    client._handle, client.terminals = cast(Any, _Handle(cwd)), AcpTerminalStore()
    return client


def test_client_shows_and_binds_the_streamed_command() -> None:
    async def _go() -> None:
        client = _client()
        full = json.dumps({"command": "rm -rf build"})
        await client.session_update("s", _Model(_lazy(full[:5])))
        await client.session_update("s", _Model(_delta(full)))
        await client.request_permission("s", _Model(_permission()), [_Model(o) for o in OPTIONS])
        assert cast(Any, client._handle).seen[0]["rawInput"] == {"command": "rm -rf build"}
        await client.session_update("s", _Model(_started("rm -rf /")))
        grants = client.terminals.grants
        assert not grants.consume("/bin/bash", _kimi("rm -rf /"), root=ROOT)
        assert grants.consume("/bin/bash", _kimi("rm -rf build"), root=ROOT)

    asyncio.run(_go())


_FAKE_AGENT = textwrap.dedent(
    """
    import json, sys
    out_path, cwd, streamed = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
    TC = "3:call_1"

    def send(message):
        sys.stdout.write(json.dumps(message) + "\\n")
        sys.stdout.flush()

    def update(body):
        send({"jsonrpc": "2.0", "method": "session/update",
              "params": {"sessionId": "s", "update": dict(body, toolCallId=TC)}})

    def text(t):
        return [{"type": "content", "content": {"type": "text", "text": t}}]

    def reply(want):
        while True:
            message = json.loads(sys.stdin.readline())
            if message.get("id") == want:
                return message

    args = json.dumps({"command": "echo bound-ok"})
    if streamed:
        update({"sessionUpdate": "tool_call", "title": "Bash", "kind": "execute",
                "status": "pending", "content": text(args[:6])})
        update({"sessionUpdate": "tool_call_update", "status": "in_progress", "content": text(args)})
    send({"jsonrpc": "2.0", "id": 1, "method": "session/request_permission", "params": {
          "sessionId": "s", "toolCall": {"toolCallId": TC, "title": "Bash",
                                         "content": text("Requesting approval to Running: echo")},
          "options": [{"optionId": "approve_once", "name": "Approve once", "kind": "allow_once"}]}})
    results = {"permission": reply(1)}
    # tool.call.started upgrade, then the spawn, back-to-back (kimi order).
    update({"sessionUpdate": "tool_call_update" if streamed else "tool_call", "kind": "execute",
            "title": "Bash",
            "status": "in_progress", "rawInput": {"command": "echo bound-ok"}, "content": text(args)})
    for rid, command in ((2, "echo hijacked"), (3, "echo bound-ok")):
        send({"jsonrpc": "2.0", "id": rid, "method": "terminal/create", "params": {
              "sessionId": "s", "command": "/bin/sh",
              "args": ["-c", "cd '" + cwd + "' && " + command], "cwd": cwd}})
        results[command] = reply(rid)
    terminal_id = results["echo bound-ok"].get("result", {}).get("terminalId")
    if terminal_id:
        send({"jsonrpc": "2.0", "id": 4, "method": "terminal/wait_for_exit",
              "params": {"sessionId": "s", "terminalId": terminal_id}})
        reply(4)
        send({"jsonrpc": "2.0", "id": 5, "method": "terminal/release",
              "params": {"sessionId": "s", "terminalId": terminal_id}})
        reply(5)
    with open(out_path, "w") as fh:
        json.dump(results, fh)
    """
)


@pytest.mark.skipif(not os.path.exists("/bin/sh"), reason="needs /bin/sh")
@pytest.mark.parametrize("streamed", [True, False])
def test_kimi_event_order_binds_over_the_real_acp_dispatch(tmp_path: Path, streamed: bool) -> None:
    script, out = tmp_path / "agent.py", tmp_path / "out.json"
    script.write_text(_FAKE_AGENT)
    cwd = os.path.realpath(tmp_path)
    client = _client(cwd)

    async def _go() -> None:
        argv = (str(script), str(out), cwd, "1" if streamed else "0")
        async with spawn_agent_process(cast(Any, client), sys.executable, *argv) as (_c, proc):
            await asyncio.wait_for(proc.wait(), timeout=30)
        await client.terminals.close_all()

    asyncio.run(_go())
    results = json.loads(out.read_text())
    assert results["permission"]["result"]["outcome"]["optionId"] == "approve_once"
    shown = cast(Any, client._handle).seen[0].get("rawInput")
    assert shown == ({"command": "echo bound-ok"} if streamed else None)
    # A different command cannot spend the grant; the bound one can.
    assert "error" in results["echo hijacked"]
    assert results["echo bound-ok"]["result"]["terminalId"]

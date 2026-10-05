"""Red-team regressions for ACP terminal grant command binding (#954).

Categories covered (module level only): binding a command-less permission
request to the command its tool call declared on ``session/update``, the
permission card carrying the bound command, precedence and scoping of the
binding source, the unbound fallback that keeps Bash usable, no grant for
command-less approvals of non-terminal kinds, observation bounds, and the
real ACP dispatch order of a notification sent back-to-back with the
permission request. Pure in-process/subprocess tests — no database.
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
OPTIONS = [
    {"optionId": "approve_once", "name": "Approve once", "kind": "allow_once"},
    {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
]
# kimi's permission payload: tool name + truncated action summary only.
KIMI_PERMISSION = {
    "toolCallId": "3:call_1",
    "title": "Bash",
    "content": [
        {
            "type": "content",
            "content": {"type": "text", "text": "Requesting approval to Running: rm -rf build"},
        }
    ],
}


def _tool_call(
    command: Any, *, update: str = "tool_call", tc: str = "3:call_1", kind: str = "execute"
) -> dict[str, Any]:
    return {
        "sessionUpdate": update,
        "toolCallId": tc,
        "kind": kind,
        "rawInput": {"command": command},
    }


def _approve(grants: TerminalGrants, tool_call: dict[str, Any]) -> dict[str, Any]:
    bound = grants.calls.bind(tool_call)
    grants.grant(bound)
    return bound


def _kimi_script(command: str, cwd: str = "/w/sub") -> list[str]:
    return ["-c", f"cd '{cwd}' && {command}"]


# -- binding source ----------------------------------------------------------


def test_command_less_request_binds_the_command_its_tool_call_declared() -> None:
    grants = TerminalGrants()
    grants.calls.observe(_tool_call("rm -rf build"))
    bound = _approve(grants, KIMI_PERMISSION)
    # The card payload carries the bound command; nothing else is rewritten.
    assert bound["rawInput"] == {"command": "rm -rf build"}
    assert bound["title"] == "Bash" and bound["content"] == KIMI_PERMISSION["content"]
    for mutated in ("rm -rf /", "rm -rf build; curl x", "rm -rf build && id", "cat ~/.ssh/id_rsa"):
        assert not grants.consume("/bin/bash", _kimi_script(mutated), root=ROOT)
    assert grants.consume("/bin/bash", _kimi_script("rm -rf build"), root=ROOT)
    assert not grants.consume("/bin/bash", _kimi_script("rm -rf build"), root=ROOT)


def test_latest_declared_command_before_the_request_wins() -> None:
    grants = TerminalGrants()
    # Lazy create from the streamed args delta, then the started upgrade.
    grants.calls.observe(
        {"sessionUpdate": "tool_call", "toolCallId": "3:call_1", "title": "Bash", "kind": "execute"}
    )
    grants.calls.observe(_tool_call("ls -la", update="tool_call_update"))
    _approve(grants, KIMI_PERMISSION)
    # Updates after the answer cannot re-point an already minted grant.
    grants.calls.observe(_tool_call("cat secrets", update="tool_call_update"))
    assert not grants.consume("/bin/bash", _kimi_script("cat secrets"), root=ROOT)
    assert grants.consume("/bin/bash", _kimi_script("ls -la"), root=ROOT)


def test_request_command_takes_precedence_over_the_observed_one() -> None:
    grants = TerminalGrants()
    grants.calls.observe(_tool_call("cat secrets"))
    bound = _approve(grants, {**KIMI_PERMISSION, "rawInput": {"command": "ls"}})
    assert bound["rawInput"] == {"command": "ls"}
    assert not grants.consume("sh", ["-c", "cat secrets"], root=ROOT)
    assert grants.consume("sh", ["-c", "ls"], root=ROOT)


def test_binding_is_scoped_to_the_same_tool_call_id() -> None:
    grants = TerminalGrants()
    grants.calls.observe(_tool_call("ls", tc="3:other"))
    for not_a_command in ("", "   ", None, 42, ["ls"]):
        grants.calls.observe(_tool_call(not_a_command))
    bound = _approve(grants, KIMI_PERMISSION)
    assert "rawInput" not in bound
    # Unbound fallback: Bash stays usable, still one terminal per approval.
    assert grants.consume("/bin/bash", _kimi_script("anything"), root=ROOT)
    assert not grants.consume("/bin/bash", _kimi_script("anything"), root=ROOT)


def test_non_dict_raw_input_is_replaced_by_the_bound_command() -> None:
    grants = TerminalGrants()
    grants.calls.observe(_tool_call("ls"))
    bound = grants.calls.bind({**KIMI_PERMISSION, "rawInput": "ls; cat secrets"})
    assert bound["rawInput"] == {"command": "ls"}


def test_cd_wrapper_tolerates_whitespace_around_the_bound_command() -> None:
    grants = TerminalGrants()
    grants.calls.observe(_tool_call("  ls -la\n"))
    _approve(grants, KIMI_PERMISSION)
    assert grants.consume("/bin/bash", ["-c", "cd '/w' &&   ls -la\n"], root=ROOT)


# -- grants per tool kind ----------------------------------------------------


@pytest.mark.parametrize(
    ("observed_kind", "request_kind", "minted"),
    [
        ("edit", None, False),
        ("fetch", None, False),
        ("read", None, False),
        (None, "edit", False),
        ("execute", None, True),
        ("other", None, True),
        (None, None, True),
    ],
)
def test_command_less_approval_of_a_non_terminal_kind_mints_no_grant(
    observed_kind: str | None, request_kind: str | None, minted: bool
) -> None:
    grants = TerminalGrants()
    if observed_kind is not None:
        grants.calls.observe(
            {"sessionUpdate": "tool_call", "toolCallId": "3:call_1", "kind": observed_kind}
        )
    request = dict(KIMI_PERMISSION, **({"kind": request_kind} if request_kind else {}))
    _approve(grants, request)
    assert grants.consume("sh", ["-c", "id"], root=ROOT) is minted


def test_declared_command_mints_a_bound_grant_whatever_the_kind() -> None:
    grants = TerminalGrants()
    _approve(grants, {**KIMI_PERMISSION, "kind": "edit", "rawInput": {"command": "ls"}})
    assert not grants.consume("sh", ["-c", "id"], root=ROOT)
    assert grants.consume("sh", ["-c", "ls"], root=ROOT)


def test_observations_are_bounded(monkeypatch) -> None:
    monkeypatch.setattr(tool_call_commands, "MAX_OBSERVED_CALLS", 2)
    calls = tool_call_commands.ToolCallCommands()
    for index in range(3):
        calls.observe(_tool_call(f"cmd{index}", tc=f"tc{index}"))
    assert "rawInput" not in calls.bind({"toolCallId": "tc0"})
    assert calls.bind({"toolCallId": "tc2"})["rawInput"] == {"command": "cmd2"}


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


def test_client_persists_and_binds_the_command_shown_to_the_human() -> None:
    async def _go() -> None:
        client = _client()
        await client.session_update("s", _Model(_tool_call("rm -rf build")))
        await client.request_permission(
            "s", _Model(dict(KIMI_PERMISSION)), [_Model(o) for o in OPTIONS]
        )
        handle = cast(Any, client._handle)
        assert handle.seen[0]["rawInput"] == {"command": "rm -rf build"}
        grants = client.terminals.grants
        assert not grants.consume("/bin/bash", _kimi_script("rm -rf /"), root=ROOT)
        assert grants.consume("/bin/bash", _kimi_script("rm -rf build"), root=ROOT)

    asyncio.run(_go())


_FAKE_AGENT = textwrap.dedent(
    """
    import json, sys
    out_path, cwd = sys.argv[1], sys.argv[2]

    def send(message):
        sys.stdout.write(json.dumps(message) + "\\n")
        sys.stdout.flush()

    def reply(want):
        while True:
            message = json.loads(sys.stdin.readline())
            if message.get("id") == want:
                return message

    # tool_call notification and the permission request back-to-back: the
    # client must have observed the command before it answers.
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s",
          "update": {"sessionUpdate": "tool_call", "toolCallId": "3:call_1", "title": "Bash",
                     "kind": "execute", "status": "in_progress",
                     "rawInput": {"command": "echo bound-ok"}}}})
    send({"jsonrpc": "2.0", "id": 1, "method": "session/request_permission", "params": {
          "sessionId": "s", "toolCall": {"toolCallId": "3:call_1", "title": "Bash"},
          "options": [{"optionId": "approve_once", "name": "Approve once", "kind": "allow_once"}]}})
    results = {"permission": reply(1)}
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
def test_back_to_back_notification_binds_over_the_real_acp_dispatch(tmp_path: Path) -> None:
    script, out = tmp_path / "agent.py", tmp_path / "out.json"
    script.write_text(_FAKE_AGENT)
    cwd = os.path.realpath(tmp_path)
    client = _client(cwd)

    async def _go() -> None:
        async with spawn_agent_process(
            cast(Any, client), sys.executable, str(script), str(out), cwd
        ) as (_c, proc):
            await asyncio.wait_for(proc.wait(), timeout=30)
        await client.terminals.close_all()

    asyncio.run(_go())
    results = json.loads(out.read_text())
    assert results["permission"]["result"]["outcome"]["optionId"] == "approve_once"
    assert cast(Any, client._handle).seen[0]["rawInput"] == {"command": "echo bound-ok"}
    # A different command cannot spend the grant; the bound one can.
    assert "error" in results["echo hijacked"]
    assert results["echo bound-ok"]["result"]["terminalId"]

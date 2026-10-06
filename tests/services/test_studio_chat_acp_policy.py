"""Red-team regressions for the Studio chat ACP policy layer (#921).

Categories covered (module level only): read-class auto-approval scope,
permission option normalization, terminal environment allowlist, terminal
working-directory confinement, and terminal/permission linkage. Pure
filesystem/subprocess tests — no database.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from acp import RequestError

from server.app.studio_chat import terminal_grants, terminal_policy
from server.app.studio_chat.acp_client import AcpClient
from server.app.studio_chat.permission_scope import (
    is_staging_read_only_tool_call,
    normalize_selected_option,
)
from server.app.studio_chat.terminal_grants import TerminalGrants
from server.app.studio_chat.terminal_policy import pinned_cwd
from server.app.studio_chat.terminals import AcpTerminalStore

pytestmark = pytest.mark.no_db

WS = "ws-policy"
OPTIONS = [
    {"optionId": "once", "name": "Approve once", "kind": "allow_once"},
    {"optionId": "always", "name": "Approve for session", "kind": "allow_always"},
    {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
]


@pytest.fixture
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "studio-mcp-files" / WS
    root.mkdir(parents=True)
    (root / "draft.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    return root


def _read(**fields) -> dict:
    return {"toolCallId": "tc", "title": "Read", "kind": "read", **fields}


def _scoped(tool_call: dict, cwd: Path, options=OPTIONS) -> bool:
    return is_staging_read_only_tool_call(tool_call, options, workspace_id=WS, cwd=str(cwd))


# -- read-class auto-approval scope -----------------------------------------


def test_read_inside_staging_is_auto_approvable(staging, tmp_path) -> None:
    assert _scoped(_read(locations=[{"path": str(staging / "draft.yaml")}]), tmp_path)
    relative = "data/studio-mcp-files/ws-policy/draft.yaml"
    assert _scoped(_read(rawInput={"file_path": relative}), tmp_path)
    search = {"kind": "search", "rawInput": {"pattern": "a", "path": str(staging)}}
    assert _scoped(search, tmp_path)


@pytest.mark.parametrize(
    "tool_call",
    [
        _read(),  # no declared target
        _read(locations=[{"path": ".env"}]),
        _read(rawInput={"file_path": "/etc/hosts"}),
        _read(rawInput={"path": "data/studio-mcp-files/ws-policy/../../../.env"}),
        _read(rawInput={"path": "data/studio-mcp-files/other-ws/x"}),
        _read(rawInput={"path": "data/studio-mcp-files/ws-policy", "pattern": "/etc/*"}),
        _read(rawInput={"path": "data/studio-mcp-files/ws-policy", "glob": "../*"}),
        _read(rawInput="cat .env"),
        # Nested containers are an unknown shape even beside a valid target.
        _read(
            locations=[{"path": "data/studio-mcp-files/ws-policy/draft.yaml"}],
            rawInput={"options": [{"path": "/etc/passwd"}]},
        ),
        _read(
            locations=[{"path": "data/studio-mcp-files/ws-policy/draft.yaml"}],
            rawInput={"extra": {"path": "/etc/passwd"}},
        ),
        _read(
            locations=[{"path": "data/studio-mcp-files/ws-policy/draft.yaml"}],
            rawInput={"paths": [["/etc/passwd"]]},
        ),
        {"kind": "execute", "rawInput": {"path": "data/studio-mcp-files/ws-policy"}},
        {"title": "Read", "rawInput": {"path": "data/studio-mcp-files/ws-policy"}},
    ],
)
def test_read_outside_staging_takes_the_human_path(staging, tmp_path, tool_call) -> None:
    assert not _scoped(tool_call, tmp_path)


def test_symlink_out_of_staging_is_not_auto_approvable(staging, tmp_path) -> None:
    (staging / "link").symlink_to(tmp_path / ".env")
    assert not _scoped(_read(locations=[{"path": str(staging / "link")}]), tmp_path)


def test_scoped_read_requires_a_one_shot_option(staging, tmp_path) -> None:
    only_always = [o for o in OPTIONS if o["kind"] != "allow_once"]
    tool_call = _read(locations=[{"path": str(staging / "draft.yaml")}])
    assert not _scoped(tool_call, tmp_path, options=only_always)


# -- option normalization -----------------------------------------------------


def test_option_normalization_rejects_unoffered_and_narrows_always() -> None:
    assert normalize_selected_option(OPTIONS, "forged") is None
    assert normalize_selected_option(OPTIONS, None) is None
    assert normalize_selected_option(OPTIONS, "always") == "once"
    assert normalize_selected_option(OPTIONS, "reject") == "reject"
    # No one-shot option to narrow to: refused rather than a session-wide allow.
    only_always = [OPTIONS[1], OPTIONS[2]]
    assert normalize_selected_option(only_always, "always") is None
    assert normalize_selected_option(only_always, "reject") == "reject"


# -- terminal environment allowlist -----------------------------------------


class _Env:
    def __init__(self, name: str, value: str) -> None:
        self.name, self.value = name, value


def _run_terminal(store: AcpTerminalStore, code: str, **kwargs) -> str:
    async def _go() -> str:
        created = await store.create(
            command=sys.executable,
            args=["-c", code],
            env=kwargs.get("env"),
            cwd=kwargs.get("cwd"),
            output_byte_limit=None,
            default_cwd=kwargs.get("default_cwd", "."),
        )
        await store.wait_for_exit(created.terminal_id)
        state = await store.output(created.terminal_id)
        await store.release(created.terminal_id)
        return state.output

    return asyncio.run(_go())


def test_terminal_env_excludes_server_process_secrets(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", "server-only-value")
    monkeypatch.setenv("AGENT_LEGION_DATABASE_URL", "postgresql://server-only")
    code = (
        "import os, json; print(json.dumps(sorted(os.environ)));"
        "print(os.environ.get('NO_COLOR', 'missing'))"
    )
    for env in (None, [_Env("NO_COLOR", "yes")]):
        output = _run_terminal(AcpTerminalStore(), code, env=env)
        assert "server-only" not in output
        assert "AGENT_LEGION_" not in output
        assert '"PATH"' in output
    assert "yes" in output


def test_terminal_env_drops_overrides_that_steer_program_resolution(tmp_path) -> None:
    hijack = [
        _Env("PATH", str(tmp_path)),
        _Env("LD_PRELOAD", "x.so"),
        _Env("DYLD_INSERT_LIBRARIES", "x.dylib"),
        _Env("BASH_ENV", "x.sh"),
        _Env("PYTHONPATH", str(tmp_path)),
        _Env("NODE_OPTIONS", "--require x"),
        _Env("TERM", "dumb"),
    ]
    env = terminal_policy.terminal_env(hijack)
    assert env.get("PATH") == os.environ.get("PATH")
    for key in ("LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "BASH_ENV", "PYTHONPATH", "NODE_OPTIONS"):
        assert key not in env
    assert env["TERM"] == "dumb"


SELECTOR_KEYS = (
    "PAGER",
    "GIT_PAGER",
    "MANPAGER",
    "LESSOPEN",
    "LESSCLOSE",
    "LESS",
    "EDITOR",
    "VISUAL",
    "GIT_EDITOR",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "BROWSER",
    "SHELL",
    "TERMINAL",
    "ENV",
)


def test_terminal_env_drops_program_selector_overrides() -> None:
    """Pager/editor/ssh/askpass-class overrides name a program to run; none
    may reach the child (only the base value, if any, survives)."""
    env = terminal_policy.terminal_env([_Env(key, "sh -c injected") for key in SELECTOR_KEYS])
    assert "injected" not in "".join(env.values())
    safe = [_Env("NO_COLOR", "1"), _Env("COLUMNS", "80"), _Env("TZ", "UTC"), _Env("LC_ALL", "C")]
    env = terminal_policy.terminal_env(safe)
    assert (env["NO_COLOR"], env["COLUMNS"], env["TZ"], env["LC_ALL"]) == ("1", "80", "UTC", "C")


# -- terminal working-directory confinement ---------------------------------


def _cwd_of(root: Path, requested: str | None) -> tuple[int, int]:
    with pinned_cwd(requested, str(root)) as fd:
        info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _ident(path: Path) -> tuple[int, int]:
    info = os.stat(path)
    return info.st_dev, info.st_ino


def test_terminal_cwd_is_pinned_inside_the_session_root(tmp_path) -> None:
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "escape").symlink_to(tmp_path)
    real_root = os.path.realpath(root)
    assert _cwd_of(root, None) == _ident(root)
    assert _cwd_of(root, "sub") == _ident(root / "sub")
    assert _cwd_of(root, os.path.join(real_root, "sub")) == _ident(root / "sub")
    # Outside the root, '..', and any symlinked component (even one pointing
    # back inside) are refused: the walk never follows links.
    (root / "inner").symlink_to(root / "sub")
    for outside in ("/", "..", "sub/..", str(tmp_path), "escape", "inner", "~", "missing"):
        with pytest.raises(RequestError):
            _cwd_of(root, outside)


def test_terminal_child_runs_in_the_pinned_directory_after_a_swap(tmp_path, monkeypatch) -> None:
    """Swap the checked path component for a symlink between pinning and
    spawn: the child still starts in the originally pinned directory."""
    root = tmp_path / "root"
    (root / "work").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    real_pinned = pinned_cwd

    @contextmanager
    def pin_then_swap(requested, base):
        with real_pinned(requested, base) as fd:
            (root / "work").rename(root / "work-moved")
            (root / "work").symlink_to(outside)
            yield fd

    monkeypatch.setattr("server.app.studio_chat.terminals.pinned_cwd", pin_then_swap)
    output = _run_terminal(
        AcpTerminalStore(), "import os; print(os.getcwd())", cwd="work", default_cwd=str(root)
    )
    assert os.path.realpath(output.strip()) == os.path.realpath(root / "work-moved")
    assert str(os.path.realpath(outside)) not in output


def test_terminal_store_refuses_cwd_outside_root(tmp_path) -> None:
    with pytest.raises(RequestError):
        _run_terminal(AcpTerminalStore(), "print(1)", cwd="/", default_cwd=str(tmp_path))


# -- terminal / permission linkage ------------------------------------------


def test_grants_are_one_shot_and_bound_to_the_approved_command() -> None:
    grants = TerminalGrants()
    assert not grants.consume("sh", ["-c", "ls"])
    grants.grant({"rawInput": {"command": "ls -la"}})
    # Bound grants match exactly: extending, prefixing or cd-wrapping the
    # approved command does not consume them.
    for mutated in (
        "cat secrets",
        "cat secrets; ls -la",
        "ls -la; cat secrets",
        "cd /w && ls -la",
        "cd '/w' && ls -la",
        "cd '/outside' && ls -la",
    ):
        assert not grants.consume("sh", ["-c", mutated])
    assert grants.consume("sh", ["-c", "ls -la"])
    assert not grants.consume("sh", ["-c", "ls -la"])
    grants.grant({"rawInput": {"command": "ls -la"}})
    assert grants.consume("ls", ["-la"])
    grants.grant({"toolCallId": "tc-unbound"})
    assert grants.consume("sh", ["-c", "anything"])


def test_grants_expire(monkeypatch) -> None:
    grants = TerminalGrants()
    grants.grant({"toolCallId": "tc"})
    monkeypatch.setattr(terminal_grants, "GRANT_TTL_SECONDS", -1)
    grants.grant({"toolCallId": "tc-expired"})
    clock = terminal_grants.time.monotonic() + 10_000
    monkeypatch.setattr(terminal_grants.time, "monotonic", lambda: clock)
    assert not grants.consume("sh", ["-c", "ls"])


class _Handle:
    cwd = "."
    callbacks: Any = None


def _client() -> AcpClient:
    client = AcpClient()
    client._handle, client.terminals = cast(Any, _Handle()), AcpTerminalStore()
    return client


def test_create_terminal_without_an_approved_permission_is_refused() -> None:
    async def _go() -> None:
        client = _client()
        with pytest.raises(RequestError):
            await client.create_terminal("s", sys.executable, ["-c", "print(1)"])
        client.terminals.grants.grant({"toolCallId": "tc"})
        created = await client.create_terminal("s", sys.executable, ["-c", "print(1)"])
        await client.release_terminal("s", created.terminal_id)
        with pytest.raises(RequestError):
            await client.create_terminal("s", sys.executable, ["-c", "print(1)"])

    asyncio.run(_go())


@pytest.mark.parametrize(
    ("decision", "grants"),
    [
        ({"option_id": "always"}, 1),  # human answer
        ({"option_id": "once", "via": "allow_all"}, 1),
        ({"option_id": "once", "via": "auto_approved"}, 0),
        ({"option_id": "once", "via": "auto_read_only"}, 0),
        ({"option_id": "reject"}, 0),
        ({"option_id": "forged"}, 0),
        ({"deny": True}, 0),
    ],
)
def test_only_human_or_allow_all_approvals_mint_terminal_grants(decision, grants) -> None:
    class _Callbacks:
        def on_permission_request(self, tool_call, options):
            return decision

    class _Model:
        def __init__(self, payload):
            self._payload = payload

        def model_dump(self, **_kwargs):
            return self._payload

    client = _client()
    client._handle.callbacks = cast(Any, _Callbacks())
    response = asyncio.run(
        client.request_permission("s", _Model({"toolCallId": "tc"}), [_Model(o) for o in OPTIONS])
    )
    minted = 0
    while client.terminals.grants.consume("sh", []):
        minted += 1
    assert minted == grants
    outcome = response.outcome
    if decision.get("option_id") in ("always", "once"):
        # allow_always is narrowed to the one-shot option on the wire.
        assert outcome.option_id == "once"
    elif decision.get("option_id") == "reject":
        assert outcome.option_id == "reject"
    else:
        assert outcome.outcome == "cancelled"

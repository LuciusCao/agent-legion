"""Handle-level unit tests for the studio chat ACP session (#204 batch 5).

Pins the narrowed exception families on the teardown/cancel paths of
``AcpSessionHandle`` and the session/load fallback in ``session_load``:
the closed-loop/kill races stay suppressed while genuine programming errors
propagate. Pure object-level tests (fake conn/loop/process) — no database.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from acp.exceptions import RequestError
from acp.schema import HttpMcpServer

from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.session_load import open_acp_session

pytestmark = pytest.mark.no_db

_MCP = HttpMcpServer(type="http", name="agent-legion-studio", url="http://x/mcp", headers=[])


class _Callbacks(SimpleNamespace):
    """AcpSessionCallbacks stand-in recording hook calls."""

    @classmethod
    def make(cls) -> _Callbacks:
        return cls(
            on_ready=lambda *a: None,
            on_update=lambda *a: None,
            on_permission_request=lambda *a: {},
            on_turn_end=lambda *a: None,
            on_turn_error=lambda *a: None,
            on_error=lambda *a: None,
            on_exit=lambda *a: None,
            runtime=None,
        )


def _handle() -> AcpSessionHandle:
    return AcpSessionHandle(
        command="true",
        args=[],
        cwd=".",
        mcp_server=_MCP,
        env=None,
        callbacks=_Callbacks.make(),
    )


# -- cancel(): the closed-loop hand-off race -------------------------------


class _FakeLoop:
    def __init__(self, error: BaseException | None) -> None:
        self._error = error
        self.calls: list[str] = []

    def call_soon_threadsafe(self, callback, *args):  # noqa: ANN001, ANN202
        self.calls.append("called")
        if self._error is not None:
            raise self._error


@pytest.mark.parametrize(
    "race_error",
    [RuntimeError("Event loop is closed"), OSError("socket closed")],
    ids=["loop-closed", "self-pipe-closed"],
)
def test_cancel_swallows_the_closed_loop_race(race_error: BaseException) -> None:
    """The teardown race (loop stopped between the state check and the
    hand-off) stays suppressed — a late cancel is a no-op, never a 500."""
    handle = _handle()
    with handle._state_lock:
        handle._loop = _FakeLoop(race_error)
        handle._conn = object()
        handle._acp_session_id = "s-1"

    handle.cancel()  # must not raise


def test_cancel_surfaces_a_programming_error_in_the_hand_off() -> None:
    """#204 narrowing: an error that is NOT the closed-loop race propagates —
    a broken hand-off must not be swallowed as a silent no-op cancel."""
    handle = _handle()
    with handle._state_lock:
        handle._loop = _FakeLoop(TypeError("bad hand-off"))
        handle._conn = object()
        handle._acp_session_id = "s-1"

    with pytest.raises(TypeError, match="bad hand-off"):
        handle.cancel()


def test_cancel_without_connection_is_a_silent_noop() -> None:
    handle = _handle()
    handle.cancel()  # no loop/conn yet: nothing to hand off, no error


# -- _kill_process(): the already-dead-child race ---------------------------


def test_kill_process_swallows_the_already_dead_race() -> None:
    """The child dying between the returncode check and the signal is the
    expected teardown race and stays suppressed."""
    handle = _handle()

    class _DeadProcess:
        returncode = 0  # poll says alive...

        def kill(self) -> None:
            raise ProcessLookupError(3)

    with handle._state_lock:
        handle._process = _DeadProcess()

    handle._kill_process()  # must not raise


def test_kill_process_surfaces_a_real_signal_failure() -> None:
    """#204 narrowing: a kill failure that is not the reaped-child race
    propagates instead of silently skipping the escalation."""
    handle = _handle()

    class _BrokenProcess:
        returncode = None

        def kill(self) -> None:
            raise AssertionError("kill path broken")

    with handle._state_lock:
        handle._process = _BrokenProcess()

    with pytest.raises(AssertionError, match="kill path broken"):
        handle._kill_process()


# -- session_load(): the fallback family ------------------------------------


class _Conn:
    def __init__(self, load_error: BaseException | None) -> None:
        self._load_error = load_error
        self.new_session_calls = 0

    async def load_session(self, **kwargs):  # noqa: ANN003, ANN202
        if self._load_error is not None:
            raise self._load_error
        return {}

    async def new_session(self, **kwargs):  # noqa: ANN003, ANN202
        self.new_session_calls += 1
        return SimpleNamespace(session_id="fresh-1")


def test_session_load_falls_back_on_agent_refusal() -> None:
    """A JSON-RPC refusal (RequestError) is the expected business failure:
    fall back to session/new, never fail the resume."""

    async def _run() -> tuple[str, bool]:
        conn = _Conn(load_error=RequestError(-32000, "no such session"))
        return await open_acp_session(
            conn,
            cwd=".",
            mcp_server=_MCP,
            resume_acp_session_id="old-1",
            capabilities={"loadSession": True},
        )

    session_id, loaded = asyncio.run(_run())

    assert session_id == "fresh-1"
    assert loaded is False


def test_session_load_load_success_short_circuits() -> None:
    async def _run() -> tuple[str, bool]:
        conn = _Conn(load_error=None)
        return await open_acp_session(
            conn,
            cwd=".",
            mcp_server=_MCP,
            resume_acp_session_id="old-1",
            capabilities={"loadSession": True},
        )

    session_id, loaded = asyncio.run(_run())

    assert session_id == "old-1"
    assert loaded is True


def test_session_load_programming_error_fails_the_resume() -> None:
    """#204 narrowing: OUR call path breaking (anything outside the RequestError
    family) must fail the resume loudly — a silent session/new fallback would
    drop the resumed context with no record that the load path is broken."""

    async def _run() -> tuple[str, bool]:
        conn = _Conn(load_error=TypeError("broken call path"))
        return await open_acp_session(
            conn,
            cwd=".",
            mcp_server=_MCP,
            resume_acp_session_id="old-1",
            capabilities={"loadSession": True},
        )

    with pytest.raises(TypeError, match="broken call path"):
        asyncio.run(_run())


def test_session_load_skipped_without_capability() -> None:
    """No loadSession advertisement: straight to session/new (the flag gates
    the attempt, so an unadvertised load never happens)."""

    async def _run() -> tuple[str, bool]:
        conn = _Conn(load_error=AssertionError("must not be called"))
        return await open_acp_session(
            conn,
            cwd=".",
            mcp_server=_MCP,
            resume_acp_session_id="old-1",
            capabilities={"loadSession": False},
        )

    session_id, loaded = asyncio.run(_run())

    assert session_id == "fresh-1"
    assert loaded is False


# Guard against drift: the fake loop must still mimic the real asyncio one
# (call_soon_threadsafe raises RuntimeError once closed).
def test_asyncio_call_soon_threadsafe_contract() -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.call_soon_threadsafe(lambda: None)
        loop.close()
        with pytest.raises(RuntimeError, match="Event loop is closed"):
            loop.call_soon_threadsafe(lambda: None)
    finally:
        loop.close()

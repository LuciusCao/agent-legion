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

from server.app.studio_chat import acp_session as acp_session_module
from server.app.studio_chat import prompt_turn
from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.prompt_turn import PromptWedgedError
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
            on_turn_end=lambda *a, **k: None,
            on_turn_timeout=lambda *a: None,
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


# -- request_stop(): graceful post-turn exit vs close()'s full teardown -----


def test_request_stop_does_not_gate_a_later_close() -> None:
    """#611 codex P1: request_stop() must NOT flip _closed — a resume racing
    in mid-turn still needs close()'s full join→kill teardown of this runtime
    before the new one spawns. request_stop only enqueues _CLOSE + remembers
    it asked; close() afterwards still enqueues its own _CLOSE and runs the
    join path (no early return at the idempotence gate)."""
    handle = _handle()
    handle.request_stop()
    assert handle._closed is False  # the whole point: not gated

    closed_seen: list[bool] = []
    original_put = handle._queue.put

    def _recording_put(item: object) -> None:
        closed_seen.append(handle._closed)
        original_put(item)

    handle._queue.put = _recording_put  # type: ignore[method-assign]
    # Simulate the resume teardown path: no thread ever started, but close()
    # must still pass its gate and enqueue (the no-thread early return comes
    # AFTER the gate + put, so the gate behavior is what we assert on).
    handle.close()
    assert closed_seen == [True]  # close() ran its own gate + enqueue
    assert handle._closed is True


def test_request_stop_is_idempotent() -> None:
    """Repeat request_stop calls enqueue exactly one _CLOSE (the second call
    sees _stop_requested and returns early)."""
    from server.app.studio_chat.acp_session import _CLOSE

    handle = _handle()
    handle.request_stop()
    handle.request_stop()
    assert handle._queue.qsize() == 1
    assert handle._queue.get_nowait() is _CLOSE
    assert handle._closed is False


def test_request_stop_after_close_is_a_noop() -> None:
    """A close()d handle ignores request_stop: the runtime is already being
    torn down; a second _CLOSE would be harmless but noisy."""
    from server.app.studio_chat.acp_session import _CLOSE

    handle = _handle()
    handle.close()
    handle.request_stop()
    assert handle._queue.qsize() == 1
    assert handle._queue.get_nowait() is _CLOSE


# -- _kill_process(): the already-dead-child race ---------------------------


def test_kill_process_swallows_the_already_dead_race() -> None:
    """The child dying between the returncode check and the signal is the
    expected teardown race and stays suppressed."""
    handle = _handle()

    class _DeadProcess:
        returncode = None  # poll says alive; dies before the signal lands

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

    async def _run():
        conn = _Conn(load_error=RequestError(-32000, "no such session"))
        return await open_acp_session(
            conn,
            cwd=".",
            mcp_server=_MCP,
            resume_acp_session_id="old-1",
            capabilities={"loadSession": True},
        )

    session_id, loaded, _modes, _config_options = asyncio.run(_run())

    assert session_id == "fresh-1"
    assert loaded is False


def test_session_load_load_success_short_circuits() -> None:
    async def _run():
        conn = _Conn(load_error=None)
        return await open_acp_session(
            conn,
            cwd=".",
            mcp_server=_MCP,
            resume_acp_session_id="old-1",
            capabilities={"loadSession": True},
        )

    session_id, loaded, _modes, _config_options = asyncio.run(_run())

    assert session_id == "old-1"
    assert loaded is True


def test_session_load_programming_error_fails_the_resume() -> None:
    """#204 narrowing: OUR call path breaking (anything outside the RequestError
    family) must fail the resume loudly — a silent session/new fallback would
    drop the resumed context with no record that the load path is broken."""

    async def _run():
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

    async def _run():
        conn = _Conn(load_error=AssertionError("must not be called"))
        return await open_acp_session(
            conn,
            cwd=".",
            mcp_server=_MCP,
            resume_acp_session_id="old-1",
            capabilities={"loadSession": False},
        )

    session_id, loaded, _modes, _config_options = asyncio.run(_run())

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


# -- _prompt_loop(): the #664 wedged-turn timeout ladder ----------------------


class _CancelHonoringConn:
    """Fake ACP conn whose prompt turns each finish only after their own
    session/cancel (turn N waits for cancel N)."""

    def __init__(self) -> None:
        self.cancel_calls: list[str] = []
        self.prompt_calls = 0

    async def prompt(self, session_id, blocks):  # noqa: ANN001, ANN202
        self.prompt_calls += 1
        while len(self.cancel_calls) < self.prompt_calls:
            await asyncio.sleep(0.005)
        return SimpleNamespace(stop_reason="cancelled")

    async def cancel(self, session_id):  # noqa: ANN001, ANN202
        self.cancel_calls.append(session_id)


class _WedgedConn:
    """Fake ACP conn that never answers a prompt (wedged stdio transport)."""

    def __init__(self) -> None:
        self.cancel_calls = 0

    async def prompt(self, session_id, blocks):  # noqa: ANN001, ANN202
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def cancel(self, session_id):  # noqa: ANN001, ANN202
        self.cancel_calls += 1


class _CancelFailingConn(_WedgedConn):
    async def cancel(self, session_id):  # noqa: ANN001, ANN202
        self.cancel_calls += 1
        raise OSError("stdio pipe closed")


class _RefusingConn:
    """Fake ACP conn failing the turn in time (the ordinary agent refusal)."""

    def __init__(self) -> None:
        self.cancel_calls = 0

    async def prompt(self, session_id, blocks):  # noqa: ANN001, ANN202
        raise RequestError(-32000, "another turn is already in progress")

    async def cancel(self, session_id):  # noqa: ANN001, ANN202
        self.cancel_calls += 1


class _QuickConn:
    """Fake ACP conn answering immediately (no ladder involvement)."""

    async def prompt(self, session_id, blocks):  # noqa: ANN001, ANN202
        return SimpleNamespace(stop_reason="end_turn")

    async def cancel(self, session_id):  # noqa: ANN001, ANN202
        raise AssertionError("no cancel for an in-time turn")


class _PermissionParkedConn:
    """Fake conn parked until BOTH the settle hook and the cancel arrive —
    mirrors an agent that can only end its prompt after the permission
    reply (session/request_permission) it is waiting on."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def prompt(self, session_id, blocks):  # noqa: ANN001, ANN202
        while "cancel" not in self.events:
            await asyncio.sleep(0.005)
        return SimpleNamespace(stop_reason="cancelled")

    async def cancel(self, session_id):  # noqa: ANN001, ANN202
        self.events.append("cancel")


@pytest.fixture
def short_turn_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject near-zero ladder timeouts so the wedged paths never really wait."""
    monkeypatch.setattr(prompt_turn, "PROMPT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(prompt_turn, "CANCEL_GRACE_SECONDS", 0.5)


def _record_callbacks(handle: AcpSessionHandle) -> dict[str, list]:
    calls: dict[str, list] = {
        "turn_end": [],
        "turn_end_timed_out": [],
        "turn_error": [],
        "error": [],
    }

    def _on_turn_end(stop_reason: str, *, timed_out: bool = False) -> None:
        calls["turn_end"].append(stop_reason)
        calls["turn_end_timed_out"].append(timed_out)

    handle.callbacks.on_turn_end = _on_turn_end
    handle.callbacks.on_turn_error = calls["turn_error"].append
    handle.callbacks.on_error = calls["error"].append
    return calls


@pytest.mark.usefixtures("short_turn_timeouts")
def test_prompt_timeout_cancels_and_the_session_continues() -> None:
    """#664 path 1: the timeout sends session/cancel to the agent; an agent
    that honours it ends the turn (stop_reason=cancelled) through on_turn_end
    and the loop serves the next prompt — no zombie session. #693: the
    timeout-terminated turn is flagged to the callback so the service can
    surface it instead of reporting success."""
    from server.app.studio_chat.acp_session import _CLOSE

    handle = _handle()
    calls = _record_callbacks(handle)
    handle._queue.put("first")
    handle._queue.put("second")
    handle._queue.put(_CLOSE)
    conn = _CancelHonoringConn()

    asyncio.run(handle._prompt_loop(conn, "s-1"))

    assert conn.cancel_calls == ["s-1", "s-1"]  # one cancel per timed-out turn
    assert calls == {
        "turn_end": ["cancelled", "cancelled"],
        "turn_end_timed_out": [True, True],
        "turn_error": [],
        "error": [],
    }


@pytest.mark.usefixtures("short_turn_timeouts")
def test_in_time_turn_reports_not_timed_out() -> None:
    """#693: a turn that finishes inside the timeout reports timed_out=False
    and never touches the ladder (no cancel, no timeout hook)."""
    from server.app.studio_chat.acp_session import _CLOSE

    handle = _handle()
    calls = _record_callbacks(handle)
    handle._queue.put("quick")
    handle._queue.put(_CLOSE)

    asyncio.run(handle._prompt_loop(_QuickConn(), "s-1"))

    assert calls == {
        "turn_end": ["end_turn"],
        "turn_end_timed_out": [False],
        "turn_error": [],
        "error": [],
    }


@pytest.mark.usefixtures("short_turn_timeouts")
def test_prompt_grace_exhausted_escalates_wedged() -> None:
    """#664 path 2 (loop level): the prompt task never completes after the
    cancel either, so the loop raises PromptWedgedError for _run's fatal
    path instead of containing the turn per-turn."""
    handle = _handle()
    calls = _record_callbacks(handle)
    handle._queue.put("stuck")
    conn = _WedgedConn()

    with pytest.raises(PromptWedgedError, match="wedged"):
        asyncio.run(handle._prompt_loop(conn, "s-1"))

    assert conn.cancel_calls == 1  # the cancel WAS attempted before failing
    assert calls == {"turn_end": [], "turn_end_timed_out": [], "turn_error": [], "error": []}


@pytest.mark.usefixtures("short_turn_timeouts")
def test_prompt_cancel_send_failure_escalates_wedged() -> None:
    """#664 path 2 variant: a cancel that cannot even be sent is the same
    transport death — wedged escalation with the cause chained."""
    handle = _handle()
    handle._queue.put("stuck")
    conn = _CancelFailingConn()

    with pytest.raises(PromptWedgedError, match="session/cancel failed"):
        asyncio.run(handle._prompt_loop(conn, "s-1"))

    assert conn.cancel_calls == 1


@pytest.mark.usefixtures("short_turn_timeouts")
def test_run_wedged_turn_marks_error_and_tears_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """#664 path 2 (run level): PromptWedgedError falls into _run's fatal
    path — on_error (fatal → session error, the resume-reachable state)
    fires and the async-with tears the subprocess down."""

    class _WedgedRunConn(_WedgedConn):
        async def initialize(self, **kwargs):  # noqa: ANN003, ANN202
            return SimpleNamespace(agent_capabilities=None, agent_info=None)

    class _FakeSpawn:
        def __init__(self) -> None:
            self.conn = _WedgedRunConn()
            self.exited = False

        async def __aenter__(self):  # noqa: ANN202
            return self.conn, SimpleNamespace(stderr=None)

        async def __aexit__(self, *exc_info):  # noqa: ANN002, ANN202
            self.exited = True

    async def _fake_open(**kwargs):  # noqa: ANN003, ANN202
        return SimpleNamespace(acp_session_id="s-1", loaded_existing=False)

    spawn = _FakeSpawn()
    monkeypatch.setattr(acp_session_module, "spawn_agent_process", lambda *a, **k: spawn)
    monkeypatch.setattr(acp_session_module, "open_acp_session", _fake_open)
    handle = _handle()
    calls = _record_callbacks(handle)
    handle._queue.put("stuck")

    asyncio.run(handle._run())

    assert len(calls["error"]) == 1 and "wedged" in calls["error"][0]
    assert calls["turn_end"] == [] and calls["turn_error"] == []
    assert spawn.exited is True  # the subprocess context was torn down


@pytest.mark.usefixtures("short_turn_timeouts")
def test_prompt_turn_failure_stays_per_turn_containment() -> None:
    """#664 invariant: a non-timeout turn failure (agent refusal) is still
    contained per-turn — on_turn_error only, no cancel sent, loop alive."""
    from server.app.studio_chat.acp_session import _CLOSE

    handle = _handle()
    calls = _record_callbacks(handle)
    handle._queue.put("boom")
    handle._queue.put(_CLOSE)
    conn = _RefusingConn()

    asyncio.run(handle._prompt_loop(conn, "s-1"))  # drains _CLOSE and returns

    assert len(calls["turn_error"]) == 1
    assert "another turn is already in progress" in calls["turn_error"][0]
    assert calls["turn_end"] == [] and calls["error"] == []
    assert conn.cancel_calls == 0  # in-time failure never triggers the ladder


@pytest.mark.usefixtures("short_turn_timeouts")
def test_on_turn_end_callback_failure_stays_per_turn() -> None:
    """#664 review: on_turn_end is back inside the per-turn containment — a
    transient callback failure (store/DB inside the hook) goes to
    on_turn_error and the loop keeps serving, exactly as before #664; only
    PromptWedgedError crosses the turn boundary."""
    from server.app.studio_chat.acp_session import _CLOSE

    handle = _handle()
    calls = _record_callbacks(handle)

    def _failing_turn_end(stop_reason: str, *, timed_out: bool = False) -> None:
        raise RuntimeError("store down")

    handle.callbacks.on_turn_end = _failing_turn_end
    handle._queue.put("one")
    handle._queue.put(_CLOSE)

    asyncio.run(handle._prompt_loop(_QuickConn(), "s-1"))  # drains _CLOSE

    assert calls["turn_error"] == ["RuntimeError: store down"]
    assert calls["error"] == []  # never escalated to the fatal path


@pytest.mark.usefixtures("short_turn_timeouts")
def test_prompt_timeout_settles_permissions_before_cancel() -> None:
    """#664 review: the auto-cancel path fires the on_turn_timeout hook
    (service settles parked permissions as denied) strictly BEFORE
    session/cancel — an agent parked on a permission reply can then end its
    turn, so the healthy session is not misread as wedged."""
    from server.app.studio_chat.acp_session import _CLOSE

    handle = _handle()
    calls = _record_callbacks(handle)
    conn = _PermissionParkedConn()
    handle.callbacks.on_turn_timeout = lambda: conn.events.append("settle")
    handle._queue.put("parked")
    handle._queue.put(_CLOSE)

    asyncio.run(handle._prompt_loop(conn, "s-1"))

    assert conn.events == ["settle", "cancel"]  # hook strictly before cancel
    assert calls == {
        "turn_end": ["cancelled"],
        "turn_end_timed_out": [True],
        "turn_error": [],
        "error": [],
    }

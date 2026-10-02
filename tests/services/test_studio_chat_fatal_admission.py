"""A dying ACP consumer fences admission before blocking cleanup/callbacks."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server.app.studio_chat import acp_session as module

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("boundary", ["transport", "error", "startup", "thread", "exit"])
def test_exit_fences_admission_before_blocking_boundary(monkeypatch, boundary):
    entered = threading.Event()
    release = threading.Event()
    callbacks = Mock()
    handle = module.AcpSessionHandle(
        command="unused", args=[], cwd=".", mcp_server=None, env=None, callbacks=callbacks
    )

    def block(*args, **kwargs):
        entered.set()
        assert release.wait(5), "test did not release the lifecycle boundary"

    class Spawn:
        async def __aenter__(self):
            if boundary == "startup":
                raise ConnectionError("startup transport failed")
            return SimpleNamespace(initialize=initialize), SimpleNamespace(stderr=None)

        async def __aexit__(self, *args):
            if boundary == "transport":
                block()

    async def initialize(**kwargs):
        return SimpleNamespace(agent_capabilities=None, agent_info=None)

    async def opened(*args, **kwargs):
        return SimpleNamespace(acp_session_id="test", loaded_existing=False)

    async def failed_loop(*args):
        raise ConnectionError("consumer failed")

    monkeypatch.setattr(module, "spawn_agent_process", lambda *a, **k: Spawn())
    monkeypatch.setattr(module, "open_acp_session", opened)
    monkeypatch.setattr(handle, "_prompt_loop", failed_loop)
    if boundary == "thread":
        monkeypatch.setattr(handle, "_run", failed_loop)
    elif boundary == "exit":

        async def normal_exit():
            return

        monkeypatch.setattr(handle, "_run", normal_exit)
        callbacks.on_exit.side_effect = block
    if boundary in {"error", "startup", "thread"}:
        callbacks.on_error.side_effect = block

    handle.start()
    accepted = Mock()
    try:
        assert entered.wait(5), "lifecycle boundary was not reached"
        assert handle.send_prompt("must not enter history", accept=accepted) is False
        accepted.assert_not_called()
        # Stopping admission must not claim explicit-close ownership: close()
        # still needs to join/kill, and on_exit must report an unexpected death.
        assert not handle._closed
    finally:
        release.set()
        handle._thread.join(5)
    assert not handle._thread.is_alive()
    callbacks.on_exit.assert_called_once_with(close_initiated=False)
    handle.close()
    assert handle._closed


def test_recoverable_turn_failure_keeps_admission_open(monkeypatch):
    callbacks = Mock()
    handle = module.AcpSessionHandle(
        command="unused", args=[], cwd=".", mcp_server=None, env=None, callbacks=callbacks
    )

    async def refused(*args, **kwargs):
        raise ValueError("recoverable refusal")

    accepted = Mock()

    def on_error(detail):
        assert handle.send_prompt("retry", accept=accepted)
        # End this isolated loop without changing the admission state.
        handle._queue.get_nowait()
        handle._queue.put(module._CLOSE)

    monkeypatch.setattr(module, "run_prompt_turn", refused)
    callbacks.on_turn_error.side_effect = on_error
    handle.send_prompt("first")
    asyncio.run(handle._prompt_loop(None, "test"))
    accepted.assert_called_once()
    callbacks.on_error.assert_not_called()

"""Deterministic startup/shutdown interleavings with real ACP and token storage."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from server.app.auth.sessions import hash_token
from server.app.services.job_errors import ConflictError
from server.app.studio_chat import spawn as spawn_module
from tests.helpers import studio_chat_fixtures as resume_tests
from tests.helpers import wait_for_predicate

chat = resume_tests.chat


@pytest.mark.parametrize("operation", ["create", "resume"])
@pytest.mark.parametrize("phase", ["mint", "registered"])
@pytest.mark.parametrize("fail", [False, True])
def test_shutdown_drains_inflight_startup(chat, monkeypatch, operation, phase, fail):
    service, _bus, register, workspace, user = chat
    register(resume_tests.TEXT_SCRIPT)
    prior = None
    if operation == "resume":
        sid = service.create_session(workspace, user, "fake-agent")["id"]
        prior = service.runtime(sid)
        service.db.update_studio_chat_session(sid, status="error")

    entered, release = threading.Event(), threading.Event()
    tokens, handles = [], []
    real_mint = spawn_module.mint_scoped_token
    real_start = spawn_module.AcpSessionHandle.start

    def pause():
        entered.set()
        assert release.wait(20), "startup was not released"
        if fail:
            raise RuntimeError("injected startup failure")

    def mint(*args, **kwargs):
        if phase == "mint":
            pause()
        token = real_mint(*args, **kwargs)
        tokens.append(token)
        return token

    def start(handle):
        handles.append(handle)
        if phase == "registered":
            pause()
        real_start(handle)

    monkeypatch.setattr(spawn_module, "mint_scoped_token", mint)
    monkeypatch.setattr(spawn_module.AcpSessionHandle, "start", start)

    def launch():
        if operation == "resume":
            return service.resume_session(sid, workspace, user)
        return service.create_session(workspace, user, "fake-agent")

    with ThreadPoolExecutor(max_workers=3) as pool:
        startup = pool.submit(launch)
        try:
            assert entered.wait(10)
            shutdown = pool.submit(service.shutdown)
            wait_for_predicate(lambda: service._lifecycle._sealed, timeout=5)
            second_shutdown = pool.submit(service.shutdown)
            assert not shutdown.done()
            assert not second_shutdown.done()
            # Both entry points reject before touching rows or minting tokens.
            with pytest.raises(ConflictError, match="shutting down"):
                service.create_session(workspace, user, "fake-agent")
            with pytest.raises(ConflictError, match="shutting down"):
                service.resume_session("unused", workspace, user)
        finally:
            release.set()
        if fail:
            with pytest.raises(RuntimeError, match="injected startup failure"):
                startup.result(timeout=20)
        else:
            startup.result(timeout=20)
        shutdown.result(timeout=20)
        second_shutdown.result(timeout=20)

    assert not service._runtimes
    assert all(service.db.get_scoped_token_user(hash_token(token)) is None for token in tokens)
    for handle in handles + ([prior.handle] if prior else []):
        assert handle._closed
        assert handle._thread is None or not handle._thread.is_alive()
    assert all(
        session["status"] in ("closed", "error")
        for session in service.db.list_studio_chat_sessions(workspace)
    )
    service.shutdown()  # repeated shutdown remains harmless

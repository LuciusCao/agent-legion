"""Direct stub handles must satisfy the real TurnLease fatal identity contract."""

from __future__ import annotations

import pytest

from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.service import StudioChatService
from server.app.studio_chat.turn_watchdog import begin_turn_admission
from tests.helpers import wait_for_predicate
from tests.services import test_studio_chat_compaction as compaction
from tests.services import test_studio_chat_empty_turn as empty_turn
from tests.services import test_studio_chat_resume_service as resume_service
from tests.services import test_studio_chat_service_sessions as service_sessions
from tests.services import test_studio_chat_token_keepalive as token_keepalive

_CASES = [
    (compaction._StubHandle, compaction._bind_stub_runtime),
    (empty_turn._StubHandle, empty_turn._bind_stub_runtime),
    (resume_service._StubHandle, resume_service._bind_stub_runtime),
    (service_sessions._StubHandle, service_sessions._bind_stub_runtime),
    (token_keepalive._StubHandle, token_keepalive._bind_stub_runtime),
]


def _make_runtime(job_db, settings, handle):
    service = StudioChatService(job_db, settings, None)
    workspace_id = job_db.create_workspace(name="Stub contract")["id"]
    user_id = str(job_db.create_user("stub-user", password_hash=None)["id"])
    session_id = job_db.create_studio_chat_session(workspace_id, user_id, "direct-agent")
    job_db.update_studio_chat_session(session_id, status="idle")
    runtime = SessionRuntime(handle, token="stub-token")
    with service._runtimes_lock:
        service._runtimes[session_id] = runtime
    return service, session_id, runtime


@pytest.mark.parametrize(("handle_type", "bind"), _CASES)
def test_direct_stub_supports_conservative_fatal_contract(
    job_db, settings, handle_type, bind
) -> None:
    service, session_id, runtime = _make_runtime(job_db, settings, handle_type())
    bind(service, session_id, runtime)
    lease = begin_turn_admission(runtime)
    assert lease is not None
    lease.activate()

    assert lease.service is service
    assert lease.session_id == session_id
    assert lease.callbacks.runtime is runtime
    lease.fail_claim_uncertain("stub fatal identity contract")

    process = runtime.handle._process
    assert lease.state in ("KILLING", "FATAL")
    assert lease.cleanup_thread is not None
    assert lease.process is process
    assert process.kill_calls == 1
    lease.cleanup_thread.join(timeout=5)
    wait_for_predicate(
        lambda: (
            service.runtime(session_id) is None
            and service.get_session(session_id)["status"] == "error"
        ),
        timeout=3,
    )
    assert lease.state == "FATAL"

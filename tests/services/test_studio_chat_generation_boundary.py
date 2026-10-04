"""Adversarial runtime retirement: faults and late events cannot cross generations."""

from unittest.mock import Mock

import pytest

from server.app.services.job_errors import ConflictError
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.token_admission import require_live_run_token
from server.app.studio_chat.token_keepalive import invalidate_run_token, keepalive_run_token

pytestmark = pytest.mark.no_db


@pytest.fixture
def context():
    service = Mock()
    runtime = SessionRuntime(Mock(), "dead-token")
    service.runtime.return_value = runtime
    service.db.get_scoped_token_user.return_value = None
    callbacks = ServiceCallbacks(service, "session")
    callbacks.runtime = runtime
    return service, runtime, callbacks


@pytest.mark.parametrize("entry", ["human", "keepalive", "known_dead"])
@pytest.mark.parametrize("fault", ["write", "snapshot", "notice", None])
def test_known_dead_always_stops_own_handle(context, entry, fault):
    service, runtime, _ = context
    if fault is not None:
        target = {
            "write": service.db.update_studio_chat_session_if,
            "snapshot": service.store.publish_session,
            "notice": service.store.append_message,
        }[fault]
        target.side_effect = RuntimeError("injected I/O failure")
    if entry == "human":
        with pytest.raises(ConflictError, match="继续对话"):
            require_live_run_token(service, "session", runtime)
    elif entry == "keepalive":
        keepalive_run_token(service, "session")
    else:
        invalidate_run_token(service, "session", runtime)
    runtime.handle.request_stop.assert_called_once_with()
    assert service.db.get_scoped_token_user.call_count == (0 if entry == "known_dead" else 1)
    assert runtime.token_keepalive_done == (fault is None)


@pytest.mark.parametrize("retired", ["closed", "replaced", "absent"])
def test_every_late_callback_is_fenced(context, retired):
    service, runtime, callbacks = context
    if retired == "closed":
        runtime.closed = True
    else:
        service.runtime.return_value = (
            SessionRuntime(Mock(), "new") if retired == "replaced" else None
        )
    callbacks.on_ready({}, Mock())
    callbacks.on_update({"sessionUpdate": "tool_call"})
    callbacks.on_turn_end("end_turn")
    callbacks.on_turn_timeout()
    callbacks.on_turn_error("old error")
    callbacks.on_error("old fatal error")
    for kind in ("read", "execute"):
        assert callbacks.on_permission_request({"kind": kind}, []) == {"deny": True}
    for name in ("_on_ready", "_on_update", "_on_turn_end", "_on_turn_timeout", "_on_error"):
        getattr(service, name).assert_not_called()
    assert service.db.mock_calls == []
    assert service.store.mock_calls == []
    invalidate_run_token(service, "session", runtime)
    runtime.handle.request_stop.assert_not_called()


def test_unknown_token_status_does_not_invalidate(context):
    service, runtime, _ = context
    service.db.get_scoped_token_user.side_effect = RuntimeError("DB unavailable")
    with pytest.raises(RuntimeError, match="DB unavailable"):
        require_live_run_token(service, "session", runtime)
    keepalive_run_token(service, "session")
    runtime.handle.request_stop.assert_not_called()
    service.db.update_studio_chat_session_if.assert_not_called()


@pytest.mark.parametrize("fault", [False, True])
def test_exit_publishes_while_generation_is_registered_and_always_cleans(context, fault):
    from server.app.studio_chat.events import AcpEventHandlers

    service, runtime, _ = context

    def append(*args):
        service.teardown_runtime.assert_not_called()
        assert service.runtime("session") is runtime
        if fault:
            raise RuntimeError("final projection failed")

    service.store.append_message.side_effect = append
    events = AcpEventHandlers(service)
    if fault:
        with pytest.raises(RuntimeError, match="final projection"):
            events.on_exit("session", close_initiated=False, expected=runtime)
    else:
        events.on_exit("session", close_initiated=False, expected=runtime)
    service.teardown_runtime.assert_called_once_with(
        "session", runtime, close_handle=False, expected=runtime
    )


def test_exit_that_loses_durable_transition_emits_no_error(context):
    from server.app.studio_chat.events import AcpEventHandlers

    service, runtime, _ = context
    service.db.update_studio_chat_session_if.return_value = False
    AcpEventHandlers(service).on_exit("session", close_initiated=False, expected=runtime)
    service.store.append_message.assert_not_called()
    service.teardown_runtime.assert_called_once()

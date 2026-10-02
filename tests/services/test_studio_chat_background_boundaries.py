"""Durable human admission and restored ACP sessions define completion epochs."""

from unittest.mock import Mock

import pytest

from server.app.studio_chat import acp_session
from server.app.studio_chat import background_wakeup as wake
from server.app.studio_chat.kimi_task_store import task_root
from tests.helpers import studio_chat_fixtures as resume_tests
from tests.helpers.studio_chat_fixtures import write_task

admission = resume_tests.admission
chat = resume_tests.chat


def test_completion_during_durable_acceptance_is_cancelled(admission, tmp_path, monkeypatch):
    service, db, sid, workspace, runtime = admission
    cursor = runtime.background_cursor = wake.CompletionCursor(tmp_path, "acp-1")
    wake.cancel_wakeup(runtime)
    accept = db.accept_studio_chat_message

    def finish_during_commit(*args):
        message = accept(*args)
        write_task(tmp_path, "during-commit", "completed")
        return message

    monkeypatch.setattr(db, "accept_studio_chat_message", finish_during_commit)
    service.send_message(sid, workspace, "accepted")
    assert runtime.background_wakeup_enabled
    assert "during-commit" in cursor.seen
    cursor.step(service, sid, runtime)
    assert not cursor.pending
    write_task(tmp_path, "after-rearm", "completed")
    cursor.step(service, sid, runtime)
    assert cursor.pending == {"after-rearm"}
    assert db.count_studio_chat_user_messages(sid) == 1
    assert runtime.handle._queue.qsize() == 1


@pytest.mark.parametrize("cancel_again", [False, True])
def test_failed_baseline_preserves_human_handoff_and_retries_safely(
    admission, tmp_path, monkeypatch, cancel_again
):
    service, db, sid, workspace, runtime = admission
    cursor = runtime.background_cursor = wake.CompletionCursor(tmp_path, "acp-1")
    wake.cancel_wakeup(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(cursor, "baseline", Mock(side_effect=OSError("temporary metadata failure")))
        service.send_message(sid, workspace, "must still be delivered")
    assert db.count_studio_chat_user_messages(sid) == 1
    assert runtime.handle._queue.qsize() == 1
    assert not runtime.background_wakeup_enabled
    assert runtime.background_rearm_epoch == runtime.background_epoch
    if cancel_again:
        wake.cancel_wakeup(runtime)
    write_task(tmp_path, "before-retry", "completed")
    cursor.step(service, sid, runtime)
    assert runtime.background_wakeup_enabled is not cancel_again
    assert runtime.background_rearm_epoch is None
    assert not cursor.pending


@pytest.mark.parametrize("load_existing", [False, True])
@pytest.mark.parametrize("historical_task", [False, True])
def test_resume_observes_tasks_finishing_during_load(
    chat, tmp_path, monkeypatch, load_existing, historical_task
):
    service, _bus, register, workspace, user = chat
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path / "kimi"))
    # Keep polling out of the interleaving: drive the cursor deterministically.
    monkeypatch.setattr(wake, "POLL_SECONDS", 60)
    register(resume_tests.LOAD_SCRIPT, agent_id="kimi-test")
    sid = service.create_session(workspace, user, "kimi-test")["id"]
    old_acp = service.get_session(sid)["acp_session_id"]
    service.close_session(sid, workspace)
    root = task_root(str(service._settings.root_dir), old_acp)
    if historical_task:
        write_task(root, "historical", "completed", session_id=old_acp)
    write_task(root, "in-flight", "running", session_id=old_acp)
    script = resume_tests.LOAD_SCRIPT if load_existing else resume_tests.LOAD_FAILING_SCRIPT
    register(script, agent_id="kimi-test")
    open_session = acp_session.open_acp_session

    async def finish_during_load(*args, **kwargs):
        write_task(root, "in-flight", "completed", session_id=old_acp)
        return await open_session(*args, **kwargs)

    monkeypatch.setattr(acp_session, "open_acp_session", finish_during_load)
    service.resume_session(sid, workspace, user)
    runtime = service.runtime(sid)
    assert runtime.handle.loaded_existing is load_existing
    assert runtime.background_baseline.finished == (
        frozenset({"historical"}) if historical_task else frozenset()
    )
    cursor = runtime.background_cursor
    assert ("in-flight" in cursor.seen) is not load_existing
    # Busy turns retain completions for later; historical ones never reappear.
    with runtime.lock:
        runtime.turn_open = True
        cursor.step(service, sid, runtime)
        assert cursor.pending == ({"in-flight"} if load_existing else set())

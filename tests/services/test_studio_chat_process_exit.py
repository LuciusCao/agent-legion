"""Adversarial direct-child/stream exit coverage for Studio ACP (#1153)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server.app.auth.sessions import hash_token
from server.app.studio_chat import prompt_turn
from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.background_wakeup import wake_session
from server.app.studio_chat.exit_settlement import PROCESS_EXIT_DETAIL
from server.app.studio_chat.process_exit import AcpConnectionLost
from server.app.studio_chat.runtime import PendingPermission, SessionRuntime
from server.app.studio_chat.turn_state import open_turn
from tests.helpers import pid_is_running, wait_for_predicate
from tests.helpers import studio_chat_fixtures as fixtures

chat = fixtures.chat
admission = fixtures.admission

PERMISSION_STEP = {
    "permission": {
        "toolCall": {"toolCallId": "tc-exit", "title": "Bash: exit-test"},
        "options": [
            {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
            {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
        ],
    }
}


def _wait(condition, timeout: float = 10.0) -> None:
    wait_for_predicate(condition, timeout=timeout, interval=0.02)


def _events(service, sid: str, workspace: str, name: str) -> list[dict]:
    return [
        message["content"]
        for message in service.list_messages(sid, workspace)
        if message["kind"] == "status" and message["content"].get("event") == name
    ]


def _sink(script_path: Path) -> list[dict]:
    path = Path(str(script_path) + ".sink.jsonl")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _prompt_texts(script_path: Path) -> list[str]:
    return [
        "".join(block.get("text", "") for block in entry["received"]["params"].get("prompt", []))
        for entry in _sink(script_path)
        if entry.get("received", {}).get("method") == "session/prompt"
    ]


def _kill(runtime) -> int:
    process = runtime.handle._process
    assert process is not None and process.returncode is None
    os.kill(process.pid, signal.SIGKILL)
    return process.pid


def _resume_with_text(service, script_path: Path, sid: str, workspace: str, user: str) -> None:
    script_path.write_text(json.dumps(fixtures.TEXT_SCRIPT), encoding="utf-8")
    assert service.resume_session(sid, workspace, user)["status"] == "idle"


def _finish_next_turn(service, sid: str, workspace: str, text: str) -> None:
    service.send_message(sid, workspace, text)
    _wait(
        lambda: any(
            m["kind"] == "text" and m["role"] == "agent"
            for m in service.list_messages(sid, workspace)
        )
    )


def test_kill_mid_turn_settles_permission_and_unstarted_queue_then_resumes(chat) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register({"on_prompt": [PERMISSION_STEP], "wait_for_cancel": True})
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    assert wake_session(service, sid, runtime, ["finished-task"])
    _wait(lambda: service.get_session(sid)["status"] == "awaiting_permission")
    queued = service.send_message(sid, workspace, "not started")

    pid = _kill(runtime)
    _wait(
        lambda: (
            service.get_session(sid)["status"] == "error"
            and bool(_events(service, sid, workspace, "queued_dropped"))
            and any(
                m["kind"] == "permission" and m["content"].get("status") == "resolved"
                for m in service.list_messages(sid, workspace)
            )
        )
    )
    _wait(lambda: not runtime.handle._thread.is_alive() and not pid_is_running(pid))

    dropped = _events(service, sid, workspace, "queued_dropped")
    assert [event["message_id"] for event in dropped] == [queued["id"]]
    assert not _events(service, sid, workspace, "queued_delivered")
    assert not _events(service, sid, workspace, "turn_end")
    resolved = [
        m["content"]
        for m in service.list_messages(sid, workspace)
        if m["kind"] == "permission" and m["content"].get("status") == "resolved"
    ]
    assert len(resolved) == 1
    assert resolved[0]["decision"] == {"deny": True, "via": "process_exit"}

    _resume_with_text(service, script_path, sid, workspace, user)
    _finish_next_turn(service, sid, workspace, "after fatal exit")
    assert "not started" not in _prompt_texts(script_path)[-1]


def test_kill_between_turns_is_error_without_marking_completed_message_unknown(chat) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register(fixtures.TEXT_SCRIPT)
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    _finish_next_turn(service, sid, workspace, "completed")
    _wait(lambda: service.get_session(sid)["status"] == "idle")

    _kill(runtime)
    _wait(lambda: service.get_session(sid)["status"] == "error")
    assert service.get_session(sid)["error_detail"] == PROCESS_EXIT_DETAIL
    assert not [
        event
        for event in _events(service, sid, workspace, "error")
        if event.get("outcome") == "unknown"
    ]
    _wait(lambda: service.runtime(sid) is None)

    _resume_with_text(service, script_path, sid, workspace, user)
    _finish_next_turn(service, sid, workspace, "resumed")


def test_direct_mid_turn_death_is_unknown_and_fallback_resume_does_not_replay(chat) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register({"on_prompt": [], "wait_for_cancel": True})
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    direct = service.send_message(sid, workspace, "direct unknown payload")
    _wait(lambda: len(_prompt_texts(script_path)) == 1)

    _kill(runtime)
    _wait(
        lambda: (
            service.get_session(sid)["status"] == "error"
            and any(
                event.get("message_id") == direct["id"] and event.get("outcome") == "unknown"
                for event in _events(service, sid, workspace, "error")
            )
        )
    )
    assert not [
        event
        for event in _events(service, sid, workspace, "queued_dropped")
        if event["message_id"] == direct["id"]
    ]

    _resume_with_text(service, script_path, sid, workspace, user)
    _finish_next_turn(service, sid, workspace, "fresh direct question")
    last_prompt = _prompt_texts(script_path)[-1]
    assert "fresh direct question" in last_prompt
    assert "direct unknown payload" not in last_prompt


def test_delivered_queue_death_is_unknown_and_never_auto_replayed(chat) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register(
        {
            "prompt_plans": [
                {"on_prompt": [], "wait_for_cancel": True},
                {"on_prompt": [], "wait_for_cancel": True},
            ]
        }
    )
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    assert wake_session(service, sid, runtime, ["finished-task"])
    queued = service.send_message(sid, workspace, "unknown payload")
    service.cancel(sid, workspace)
    _wait(
        lambda: (
            any(
                event["message_id"] == queued["id"]
                for event in _events(service, sid, workspace, "queued_delivered")
            )
            and len(_prompt_texts(script_path)) == 2
            and service.get_session(sid)["status"] == "running"
        )
    )

    _kill(runtime)
    _wait(
        lambda: (
            service.get_session(sid)["status"] == "error"
            and any(
                event.get("message_id") == queued["id"] and event.get("outcome") == "unknown"
                for event in _events(service, sid, workspace, "error")
            )
        )
    )
    assert not [
        event
        for event in _events(service, sid, workspace, "queued_dropped")
        if event["message_id"] == queued["id"]
    ]

    _resume_with_text(service, script_path, sid, workspace, user)
    _finish_next_turn(service, sid, workspace, "fresh question")
    last_prompt = _prompt_texts(script_path)[-1]
    assert "fresh question" in last_prompt
    assert "unknown payload" not in last_prompt


def test_direct_wedged_fatal_is_unknown_and_fallback_resume_does_not_replay(
    chat, monkeypatch
) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register({"on_prompt": [], "wait_for_cancel": True, "ignore_cancel": True})
    monkeypatch.setattr(prompt_turn, "PROMPT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(prompt_turn, "CANCEL_GRACE_SECONDS", 0)
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    direct = service.send_message(sid, workspace, "direct wedged payload")
    _wait(lambda: len(_prompt_texts(script_path)) == 1)

    _wait(
        lambda: (
            service.get_session(sid)["status"] == "error"
            and any(
                event.get("message_id") == direct["id"] and event.get("outcome") == "unknown"
                for event in _events(service, sid, workspace, "error")
            )
        )
    )
    assert "prompt turn wedged" in service.get_session(sid)["error_detail"]
    _resume_with_text(service, script_path, sid, workspace, user)
    _finish_next_turn(service, sid, workspace, "fresh after wedged")
    last_prompt = _prompt_texts(script_path)[-1]
    assert "fresh after wedged" in last_prompt
    assert "direct wedged payload" not in last_prompt


def test_queued_wedged_fatal_is_unknown_and_fallback_resume_does_not_replay(
    chat, monkeypatch
) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register(
        {
            "prompt_plans": [
                {"on_prompt": [], "wait_for_cancel": True},
                {"on_prompt": [], "wait_for_cancel": True, "ignore_cancel": True},
            ]
        }
    )
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    assert wake_session(service, sid, runtime, ["finished-task"])
    queued = service.send_message(sid, workspace, "queued wedged payload")
    service.cancel(sid, workspace)
    _wait(
        lambda: (
            any(
                event["message_id"] == queued["id"]
                for event in _events(service, sid, workspace, "queued_delivered")
            )
            and len(_prompt_texts(script_path)) == 2
            and service.get_session(sid)["status"] == "running"
        )
    )
    service._events().on_error(sid, "prompt turn wedged: injected", fatal=True)
    service._events().on_exit(sid, close_initiated=False, expected=runtime)
    _wait(
        lambda: (
            service.get_session(sid)["status"] == "error"
            and any(
                event.get("message_id") == queued["id"] and event.get("outcome") == "unknown"
                for event in _events(service, sid, workspace, "error")
            )
        )
    )
    runtime.handle.close()
    _resume_with_text(service, script_path, sid, workspace, user)
    _finish_next_turn(service, sid, workspace, "fresh queued after wedged")
    last_prompt = _prompt_texts(script_path)[-1]
    assert "fresh queued after wedged" in last_prompt
    assert "queued wedged payload" not in last_prompt


def test_failed_append_error_keeps_delivery_for_unknown_settlement(chat, monkeypatch) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register({"on_prompt": [{"fail": "ordinary agent refusal"}]})
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    direct = service.send_message(sid, workspace, "failed append payload")
    real_append = service.store.append_message
    failed = False

    def fail_failed_row(*args, **kwargs):
        nonlocal failed
        if args[3].get("outcome") == "failed" and not failed:
            failed = True
            raise RuntimeError("injected failed-row append failure")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(service.store, "append_message", fail_failed_row)
    _wait(
        lambda: (
            service.get_session(sid)["status"] == "error"
            and any(
                event.get("message_id") == direct["id"] and event.get("outcome") == "unknown"
                for event in _events(service, sid, workspace, "error")
            )
        )
    )
    _resume_with_text(service, script_path, sid, workspace, user)
    _finish_next_turn(service, sid, workspace, "fresh after append failure")
    last_prompt = _prompt_texts(script_path)[-1]
    assert "fresh after append failure" in last_prompt
    assert "failed append payload" not in last_prompt


def test_stdout_eof_is_fatal_even_while_direct_child_is_alive(chat) -> None:
    service, _bus, register, workspace, user = chat
    register({"on_prompt": [{"close_stdout": True}], "wait_for_cancel": True})
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    process = runtime.handle._process
    service.send_message(sid, workspace, "lose only stdout")

    _wait(lambda: service.get_session(sid)["status"] == "error")
    _wait(lambda: not runtime.handle._thread.is_alive() and process.returncode is not None)
    assert process.returncode != 0
    assert service.runtime(sid) is None
    assert not _events(service, sid, workspace, "turn_end")


def test_explicit_close_is_not_projected_as_process_crash(chat) -> None:
    service, _bus, register, workspace, user = chat
    register({"on_prompt": [], "wait_for_cancel": True})
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    service.send_message(sid, workspace, "close this turn")
    service.close_session(sid, workspace)

    assert service.get_session(sid)["status"] == "closed"
    assert not runtime.handle._thread.is_alive()
    assert _events(service, sid, workspace, "session_closed")
    assert not _events(service, sid, workspace, "error")


def test_agent_request_error_remains_per_turn(chat) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register(
        {
            "prompt_plans": [
                {"on_prompt": [{"fail": "ordinary agent refusal"}]},
                fixtures.TEXT_SCRIPT,
            ]
        }
    )
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    process = runtime.handle._process
    service.send_message(sid, workspace, "first")
    _wait(
        lambda: (
            service.get_session(sid)["status"] == "idle"
            and any(
                "RequestError" in event["detail"]
                for event in _events(service, sid, workspace, "error")
            )
        )
    )
    assert process.returncode is None

    _finish_next_turn(service, sid, workspace, "second")
    assert service.runtime(sid) is runtime
    assert process.returncode is None
    assert len(_prompt_texts(script_path)) == 2


def test_direct_request_error_is_not_mislabeled_unknown_after_later_death(chat) -> None:
    service, _bus, register, workspace, user = chat
    register({"on_prompt": [{"fail": "ordinary agent refusal"}]})
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    direct = service.send_message(sid, workspace, "known failed turn")
    _wait(lambda: service.get_session(sid)["status"] == "idle")
    failure = _events(service, sid, workspace, "error")[-1]
    assert failure["message_id"] == direct["id"]
    assert failure["outcome"] == "failed"

    _kill(runtime)
    _wait(lambda: service.get_session(sid)["status"] == "error")
    assert not [
        event
        for event in _events(service, sid, workspace, "error")
        if event.get("message_id") == direct["id"] and event.get("outcome") == "unknown"
    ]


def test_quiet_healthy_turn_can_still_cancel_without_exit_false_positive(chat) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register({"on_prompt": [], "wait_for_cancel": True})
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    runtime = service.runtime(sid)
    service.send_message(sid, workspace, "quiet but healthy")
    _wait(lambda: len(_prompt_texts(script_path)) == 1)
    assert service.get_session(sid)["status"] == "running"
    assert runtime.handle._process.returncode is None

    service.cancel(sid, workspace)
    _wait(lambda: service.get_session(sid)["status"] == "idle")
    assert _events(service, sid, workspace, "turn_end")[-1]["stop_reason"] == "cancelled"
    assert service.runtime(sid) is runtime


def test_turn_end_and_death_race_prioritizes_death() -> None:
    async def scenario() -> None:
        callbacks = Mock()
        handle = AcpSessionHandle(
            command="unused", args=[], cwd=".", mcp_server=None, env=None, callbacks=callbacks
        )
        prompt_started = asyncio.Event()
        release = asyncio.Event()

        class Conn:
            async def prompt(self, *args):
                prompt_started.set()
                await release.wait()
                return SimpleNamespace(stop_reason="end_turn")

        async def died() -> str:
            await release.wait()
            return "agent process exited"

        handle.send_prompt("turn")
        death = asyncio.create_task(died())
        loop = asyncio.create_task(handle._prompt_loop(Conn(), "session", death))
        await asyncio.wait_for(prompt_started.wait(), timeout=2)
        release.set()
        with pytest.raises(AcpConnectionLost):
            await asyncio.wait_for(loop, timeout=2)
        callbacks.on_turn_end.assert_not_called()
        callbacks.on_turn_error.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["append", "snapshot"])
def test_exit_projection_retries_without_duplicate_settlement(
    admission, monkeypatch, fault
) -> None:
    service, db, sid, _workspace, runtime = admission
    pending = PendingPermission("request-exit")
    runtime.pending_permissions[pending.request_id] = pending
    db.append_studio_chat_message(
        sid,
        "permission",
        "agent",
        {"request_id": pending.request_id, "status": "pending", "tool_call": {}, "options": []},
    )
    assert db.claim_studio_chat_turn(sid)
    open_turn(runtime, "")
    queued = db.enqueue_studio_chat_message(sid, hash_token(runtime.token), "retry me")
    runtime.inbound_messages[queued["id"]] = "queued"
    runtime.inbound_pending = 1

    if fault == "append":
        real_append = service.store.append_message_if_live
        failed = False

        def fail_once(*args, **kwargs):
            nonlocal failed
            if args[3].get("event") == "queued_dropped" and not failed:
                failed = True
                raise RuntimeError("injected append failure")
            return real_append(*args, **kwargs)

        monkeypatch.setattr(service.store, "append_message_if_live", fail_once)
    else:
        real_publish = service.store.publish_session
        failed = False

        def fail_once(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("injected snapshot failure")
            return real_publish(*args, **kwargs)

        monkeypatch.setattr(service.store, "publish_session", fail_once)

    with pytest.raises(RuntimeError, match="injected"):
        service._events().on_exit(sid, close_initiated=False, expected=runtime)
    service._events().on_exit(sid, close_initiated=False, expected=runtime)
    service._events().on_exit(sid, close_initiated=False, expected=runtime)

    messages = db.list_studio_chat_messages(sid)
    resolved = [
        m
        for m in messages
        if m["kind"] == "permission" and m["content"].get("status") == "resolved"
    ]
    dropped = [m for m in messages if m["content"].get("event") == "queued_dropped"]
    fatal = [
        m
        for m in messages
        if m["content"].get("event") == "error"
        and m["content"].get("detail") == PROCESS_EXIT_DETAIL
    ]
    assert len(resolved) == len(dropped) == len(fatal) == 1
    assert pending.event.is_set()
    assert db.get_studio_chat_session(sid)["status"] == "error"


def test_answered_permission_is_not_overwritten_by_exit_settlement(admission) -> None:
    service, db, sid, _workspace, runtime = admission
    pending = PendingPermission("answered-before-exit")
    runtime.pending_permissions[pending.request_id] = pending
    db.append_studio_chat_message(
        sid, "permission", "agent", {"request_id": pending.request_id, "status": "pending"}
    )
    pending.decision = {"option_id": "allow"}
    pending.event.set()  # human answered; waiter has not appended resolved yet

    service._events().on_exit(sid, close_initiated=False, expected=runtime)
    service._events().on_exit(sid, close_initiated=False, expected=runtime)

    resolved = [
        m["content"]
        for m in db.list_studio_chat_messages(sid)
        if m["kind"] == "permission" and m["content"].get("status") == "resolved"
    ]
    assert len(resolved) == 1
    assert resolved[0]["decision"] == {"option_id": "allow"}


def test_each_runtime_exit_has_its_own_error_row(chat) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register(fixtures.TEXT_SCRIPT)
    sid = service.create_session(workspace, user, "fake-agent")["id"]
    first = service.runtime(sid)
    _kill(first)
    _wait(lambda: service.get_session(sid)["status"] == "error")
    _resume_with_text(service, script_path, sid, workspace, user)
    second = service.runtime(sid)
    assert second is not first

    _kill(second)
    _wait(lambda: service.get_session(sid)["status"] == "error")
    fatal = [
        event
        for event in _events(service, sid, workspace, "error")
        if event.get("detail") == PROCESS_EXIT_DETAIL
    ]
    assert len(fatal) == 2
    assert len({event["runtime_exit_id"] for event in fatal}) == 2


def test_existing_fatal_error_detail_is_not_rewritten(admission) -> None:
    service, db, sid, _workspace, runtime = admission
    db.update_studio_chat_session(sid, status="error", error_detail="prompt turn wedged")

    service._events().on_exit(sid, close_initiated=False, expected=runtime)

    row = db.get_studio_chat_session(sid)
    assert row["error_detail"] == "prompt turn wedged"
    assert not [
        event
        for event in _events(service, sid, _workspace, "error")
        if event.get("detail") == PROCESS_EXIT_DETAIL
    ]


def test_old_exit_cannot_settle_into_successor(admission) -> None:
    service, db, sid, _workspace, runtime = admission
    pending = PendingPermission("old-request")
    runtime.pending_permissions[pending.request_id] = pending
    db.append_studio_chat_message(
        sid, "permission", "agent", {"request_id": pending.request_id, "status": "pending"}
    )
    successor = SessionRuntime(Mock(), "successor-token")
    service._runtimes[sid] = successor

    service._events().on_exit(sid, close_initiated=False, expected=runtime)

    assert service.runtime(sid) is successor
    assert not successor.closed
    assert runtime.closed
    assert not [
        m
        for m in db.list_studio_chat_messages(sid)
        if m["kind"] == "permission" and m["content"].get("status") == "resolved"
    ]
    assert not [m for m in db.list_studio_chat_messages(sid) if m["kind"] == "status"]

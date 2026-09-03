"""Mid-turn run-token keepalive + invalidation notice (#411).

Covers the fix for the studio-chat tool channel dying silently: a turn that
outlives its scoped token used to leave every tool call 401ing with no
signal on the session timeline. The ACP event handler now runs a keepalive
on each tool_call update — a live token slides forward, a dead one produces
exactly one ``run_token_invalidated`` status message. The message-list cap
semantics (newest-first window, #411 companion fix) are asserted here too.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.auth import scoped_tokens
from server.app.auth.sessions import hash_token
from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.service import StudioChatService
from tests.helpers import wait_for_predicate
from tests.postgres_support import TEST_DATABASE_URL

FAKE_AGENT = Path(__file__).resolve().parents[1] / "helpers" / "fake_acp_agent.py"


class RecordingBus:
    """EventBus stand-in capturing published (channel, payload) pairs."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def attach_loop(self, loop) -> None:
        del loop

    def publish(self, channel: str, payload: str) -> None:
        self.events.append((channel, json.loads(payload)))

    def subscribe(self, channel: str):
        raise NotImplementedError

    def unsubscribe(self, channel: str, queue) -> None:
        del channel, queue


class _StubHandle:
    """Minimal ACP handle stand-in: tests drive the service callbacks
    directly (no subprocess) to control interleaving precisely."""

    def send_prompt(self, text: str) -> bool:
        del text
        return True

    def cancel(self) -> None: ...

    def close(self) -> None: ...


def _tool_call(update_id: str) -> dict:
    return {
        "sessionUpdate": "tool_call",
        "toolCallId": update_id,
        "title": "agent-legion-studio__list_workflows",
        "kind": "other",
        "status": "completed",
    }


def _direct_session(job_db, settings, token: str):
    """Idle session row + registered runtime holding the given raw token."""
    bus = RecordingBus()
    service = StudioChatService(job_db, settings, bus)
    workspace_id = job_db.create_workspace(default_workflow_key="demo_workflow", name="Chat WS")[
        "id"
    ]
    user_id = str(job_db.create_user("keepalive-user", password_hash=None)["id"])
    session_id = job_db.create_studio_chat_session(workspace_id, user_id, "direct-agent")
    job_db.update_studio_chat_session(session_id, status="running")
    runtime = SessionRuntime(_StubHandle(), token=token)
    with service._runtimes_lock:
        service._runtimes[session_id] = runtime
    return service, bus, session_id, runtime, workspace_id


def _invalidation_messages(service, session_id: str) -> list[dict]:
    return [
        m
        for m in service.list_messages(session_id, None)
        if m["kind"] == "status" and m["content"].get("event") == "run_token_invalidated"
    ]


def _token_expiry(job_db, token: str) -> datetime:
    with job_db.connect() as conn:
        row = conn.execute(
            "select expires_at from auth_scoped_tokens where token_hash=%s", (hash_token(token),)
        ).fetchone()
    assert row is not None
    return row["expires_at"]


def test_tool_call_renews_near_expiry_run_token(job_db, settings) -> None:
    """A live-but-aging token slides forward on the first tool_call update —
    the long-turn hole that let a token die under a running turn (#411).
    45min of life sits ABOVE the turn-start 30min threshold (send_message's
    renew is a no-op there) but BELOW the keepalive threshold (a full turn
    duration plus grace), so only the tool_call keepalive can slide it."""
    user_id = str(job_db.create_user("slide-user", password_hash=None)["id"])
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    with job_db.connect() as conn:
        conn.execute(
            "update auth_scoped_tokens set expires_at = current_timestamp"
            " + interval '45 minutes' where token_hash=%s",
            (hash_token(token),),
        )
    service, _bus, session_id, _runtime, _workspace_id = _direct_session(job_db, settings, token)
    try:
        before = _token_expiry(job_db, token)
        service._on_update(session_id, _tool_call("tc-renew"))
        after = _token_expiry(job_db, token)
        assert after > before
        assert not _invalidation_messages(service, session_id)
    finally:
        service.shutdown()


def test_fresh_token_is_left_untouched_by_keepalive(job_db, settings) -> None:
    """The other side of the threshold math (#411 review): life > the
    keepalive threshold (a full turn plus grace) means the token already
    outlives the current turn — no slide, no notice, no wasted UPDATE."""
    user_id = str(job_db.create_user("fresh-user", password_hash=None)["id"])
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    service, _bus, session_id, _runtime, _workspace_id = _direct_session(job_db, settings, token)
    try:
        before = _token_expiry(job_db, token)
        service._on_update(session_id, _tool_call("tc-fresh"))
        assert _token_expiry(job_db, token) == before
        assert not _invalidation_messages(service, session_id)
    finally:
        service.shutdown()


def test_token_dying_between_check_and_update_is_detected(job_db, settings, monkeypatch) -> None:
    """The check→update race (#411 codex review): the liveness SELECT sees a
    live token, but the token is revoked before the conditional UPDATE
    commits — the slide matches zero rows. The keepalive must NOT report
    alive on the stale SELECT alone: the rowcount forces a re-check, which
    now sees the revocation and emits the notice even though no further
    tool_call ever arrives."""
    user_id = str(job_db.create_user("race-user", password_hash=None)["id"])
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    # Age the token into the keepalive threshold band so the slide attempts
    # a real UPDATE (a no-op band would also return False but for the
    # boring reason, masking the race under test).
    with job_db.connect() as conn:
        conn.execute(
            "update auth_scoped_tokens set expires_at = current_timestamp"
            " + interval '45 minutes' where token_hash=%s",
            (hash_token(token),),
        )
    service, _bus, session_id, _runtime, _workspace_id = _direct_session(job_db, settings, token)
    try:
        real_get = job_db.get_scoped_token_user
        real_extend = job_db.extend_scoped_token_expiry

        def revoking_get(token_hash: str):
            record = real_get(token_hash)
            if record is not None:
                # Revoke between the keepalive's liveness SELECT and its
                # slide UPDATE — exactly the window the rowcount covers.
                scoped_tokens.revoke_scoped_token(job_db, token)
            return record

        def failing_extend(*args, **kwargs):
            real_extend(*args, **kwargs)
            return False  # the slide found the row already revoked: 0 rows

        monkeypatch.setattr(job_db, "get_scoped_token_user", revoking_get)
        monkeypatch.setattr(job_db, "extend_scoped_token_expiry", failing_extend)
        service._on_update(session_id, _tool_call("tc-race"))
        assert len(_invalidation_messages(service, session_id)) == 1
    finally:
        service.shutdown()


def test_disabled_user_token_detected_as_dead(job_db, settings) -> None:
    """The admin-kills-access path (#411 review): disabling the user leaves
    the token row untouched but the lookup joins u.disabled_at — the
    keepalive must report dead and emit the notice."""
    user_id = str(job_db.create_user("disable-user", password_hash=None)["id"])
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    job_db.update_user(user_id, disabled=True)
    service, _bus, session_id, _runtime, _workspace_id = _direct_session(job_db, settings, token)
    try:
        service._on_update(session_id, _tool_call("tc-disabled"))
        assert len(_invalidation_messages(service, session_id)) == 1
    finally:
        service.shutdown()


def test_token_death_after_first_check_is_detected_on_next_tool_call(job_db, settings) -> None:
    """#411 review: the check must keep firing — a once-per-runtime check
    goes blind after the first one. Turn 1's tool_call sees a live token
    (renewed, no notice); the token then expires while the session idles;
    the next tool_call must detect the death and emit the notice."""
    user_id = str(job_db.create_user("latedeath-user", password_hash=None)["id"])
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    service, _bus, session_id, _runtime, _workspace_id = _direct_session(job_db, settings, token)
    try:
        service._on_update(session_id, _tool_call("tc-turn1"))
        assert not _invalidation_messages(service, session_id)
        # The session idles past the TTL: the token is now expired (a later
        # renew must not revive it — the leaked-token guarantee).
        with job_db.connect() as conn:
            conn.execute(
                "update auth_scoped_tokens set expires_at = current_timestamp"
                " - interval '1 minute' where token_hash=%s",
                (hash_token(token),),
            )
        service._on_update(session_id, _tool_call("tc-turn2"))
        messages = _invalidation_messages(service, session_id)
        assert len(messages) == 1
        # Deduplicated from then on: one timeline warning is enough.
        service._on_update(session_id, _tool_call("tc-turn2b"))
        assert len(_invalidation_messages(service, session_id)) == 1
    finally:
        service.shutdown()


def test_failed_notice_append_retries_on_next_tool_call(job_db, settings, monkeypatch) -> None:
    """#411 review: the notice append is guarded — a transient DB failure
    must not raise into on_update, must not set the done-flag (the notice
    would be permanently lost), and the next tool_call must retry it."""
    user_id = str(job_db.create_user("notice-user", password_hash=None)["id"])
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    scoped_tokens.revoke_scoped_token(job_db, token)
    service, _bus, session_id, runtime, _workspace_id = _direct_session(job_db, settings, token)
    try:
        real_append = service.store.append_message
        attempts = {"n": 0}

        def flaky_append(session_id_arg, kind, role, content):
            if kind != "status":
                return real_append(session_id_arg, kind, role, content)
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient db failure")
            return real_append(session_id_arg, kind, role, content)

        monkeypatch.setattr(service.store, "append_message", flaky_append)
        # First tool_call: the dead token is detected but the notice INSERT
        # fails — swallowed, flag stays unset, no raise.
        service._on_update(session_id, _tool_call("tc-a"))
        assert not runtime.token_keepalive_done
        assert not _invalidation_messages(service, session_id)
        # Second tool_call: the notice is retried and lands; now deduplicated.
        service._on_update(session_id, _tool_call("tc-b"))
        assert runtime.token_keepalive_done
        assert len(_invalidation_messages(service, session_id)) == 1
        service._on_update(session_id, _tool_call("tc-c"))
        assert len(_invalidation_messages(service, session_id)) == 1
    finally:
        service.shutdown()


def test_dead_token_appends_single_invalidation_notice(job_db, settings) -> None:
    """A revoked token: the first tool_call update appends exactly one
    run_token_invalidated status message; later tool_call updates (fresh
    runtime would be the resume case — same done-flag) do not repeat it."""
    user_id = str(job_db.create_user("dead-user", password_hash=None)["id"])
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    scoped_tokens.revoke_scoped_token(job_db, token)
    service, bus, session_id, _runtime, _workspace_id = _direct_session(job_db, settings, token)
    try:
        service._on_update(session_id, _tool_call("tc-1"))
        service._on_update(session_id, _tool_call("tc-2"))
        messages = _invalidation_messages(service, session_id)
        assert len(messages) == 1
        assert messages[0]["content"]["detail"]
        # The notice rides the same SSE message stream the UI already
        # consumes (store.append_message publishes on the session channel).
        assert any(
            payload.get("type") == "message"
            and payload["message"].get("content", {}).get("event") == "run_token_invalidated"
            for _channel, payload in bus.events
        )
    finally:
        service.shutdown()


def test_expired_token_is_not_revived_by_keepalive(job_db, settings) -> None:
    """Leaked-token guarantee preserved: an already-expired token reports
    dead (notice appended) and its expiry row is untouched — no revival."""
    user_id = str(job_db.create_user("expire-user", password_hash=None)["id"])
    token = scoped_tokens.mint_scoped_token(
        job_db, user_id, now=datetime.now(UTC) - timedelta(hours=3)
    )
    service, _bus, session_id, _runtime, _workspace_id = _direct_session(job_db, settings, token)
    try:
        before = _token_expiry(job_db, token)
        service._on_update(session_id, _tool_call("tc-x"))
        assert _token_expiry(job_db, token) == before
        assert len(_invalidation_messages(service, session_id)) == 1
    finally:
        service.shutdown()


def test_keepalive_db_failure_is_swallowed_and_retried(job_db, settings, monkeypatch) -> None:
    """The keepalive rides the notification path: a transient DB failure must
    not break tool_call persistence, and the next tool_call update retries
    the check (done-flag reset on failure)."""
    user_id = str(job_db.create_user("flaky-user", password_hash=None)["id"])
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    service, _bus, session_id, _runtime, _workspace_id = _direct_session(job_db, settings, token)
    try:
        real_get = job_db.get_scoped_token_user
        calls = {"n": 0}

        def flaky_get(token_hash: str):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient db failure")
            return real_get(token_hash)

        monkeypatch.setattr(job_db, "get_scoped_token_user", flaky_get)
        # First tool_call: the keepalive check raises — swallowed, and the
        # tool_call message itself must still be persisted.
        service._on_update(session_id, _tool_call("tc-a"))
        tool_calls = [
            m for m in service.list_messages(session_id, None) if m["kind"] == "tool_call"
        ]
        assert len(tool_calls) == 1
        assert not _invalidation_messages(service, session_id)
        # Retry on the next tool_call: the check now succeeds (live token,
        # no notice) — proving the done-flag was reset by the failure path.
        # Three lookups: the retry's liveness SELECT plus the rowcount
        # re-check (the fresh 2h token needs no slide, so the UPDATE
        # returns False and _token_alive re-verifies liveness).
        service._on_update(session_id, _tool_call("tc-b"))
        assert calls["n"] == 3
        assert not _invalidation_messages(service, session_id)
    finally:
        service.shutdown()


def test_keepalive_skipped_without_runtime(job_db, settings) -> None:
    """No registered runtime (teardown raced the notification): the
    keepalive is a no-op and must not raise into on_update."""
    user_id = str(job_db.create_user("noruntime-user", password_hash=None)["id"])
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    service, _bus, session_id, _runtime, workspace_id = _direct_session(job_db, settings, token)
    try:
        with service._runtimes_lock:
            service._runtimes.pop(session_id, None)
        service._on_update(session_id, _tool_call("tc-gone"))
        assert not _invalidation_messages(service, session_id)
        del workspace_id
    finally:
        service.shutdown()


def test_list_messages_cap_keeps_newest_rows(job_db, settings) -> None:
    """#411 companion: the 500-row cap must window on the NEWEST messages —
    a long session re-entered from the UI shows its latest turn, not the
    ancient first 500 rows. Ascending order of the return value is kept."""
    service, _bus, session_id, _runtime, workspace_id = _direct_session(job_db, settings, "x")
    try:
        for index in range(6):
            service.store.append_message(session_id, "text", "user", {"text": f"msg-{index}"})
        # limit=4 must return the LAST four messages in ascending order.
        rows = job_db.list_studio_chat_messages(session_id, limit=4)
        assert [m["content"]["text"] for m in rows] == [
            "msg-2",
            "msg-3",
            "msg-4",
            "msg-5",
        ]
        # Incremental after_seq refills stay exact under the same cap.
        tail = job_db.list_studio_chat_messages(session_id, after_seq=rows[0]["seq"], limit=4)
        assert [m["content"]["text"] for m in tail] == ["msg-3", "msg-4", "msg-5"]
    finally:
        service.shutdown()


def test_live_agent_tool_call_keeps_token_alive_end_to_end(job_db, settings, tmp_path) -> None:
    """End-to-end through the real ACP subprocess: a scripted tool_call turn
    against a near-expiry token slides it forward (no 401 window)."""
    bus = RecordingBus()
    service = StudioChatService(job_db, settings, bus)
    store = StudioAgentRegistryStore(TEST_DATABASE_URL)
    script = {
        "capabilities": {"loadSession": False, "mcpCapabilities": {"http": False, "sse": False}},
        "on_prompt": [
            {
                "notify": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tc-live",
                    "title": "agent-legion-studio__list_workflows",
                    "kind": "other",
                    "status": "completed",
                }
            },
            {
                "notify": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "done"},
                }
            },
        ],
    }
    script_path = tmp_path / "keepalive-script.json"
    script_path.write_text(json.dumps(script), encoding="utf-8")
    store.put(
        {
            "api_base": "http://127.0.0.1:8000",
            "agents": [
                {
                    "id": "fake-agent",
                    "label": "Fake Agent",
                    "command": sys.executable,
                    "args": [str(FAKE_AGENT), str(script_path)],
                }
            ],
        }
    )
    workspace_id = job_db.create_workspace(default_workflow_key="demo_workflow", name="Chat WS")[
        "id"
    ]
    user_id = str(job_db.create_user("e2e-user", password_hash=None)["id"])
    try:
        session = service.create_session(workspace_id, user_id, "fake-agent")
        token_hash_row = _sole_live_token_hash(job_db)
        # Age to 45 minutes BEFORE the turn starts — deterministic between
        # both thresholds: turn-start renewal (30min threshold) must leave it
        # untouched, the tool_call keepalive (65min threshold) must slide it.
        # Aging before send_message removes the old interleaving race where
        # the aging UPDATE raced the subprocess's tool_call notification.
        with job_db.connect() as conn:
            conn.execute(
                "update auth_scoped_tokens set expires_at = current_timestamp"
                " + interval '45 minutes' where token_hash=%s",
                (token_hash_row,),
            )
        before = _token_expiry_by_hash(job_db, token_hash_row)
        service.send_message(session["id"], workspace_id, "run tools")
        wait_for_predicate(
            lambda: service.get_session(session["id"])["status"] == "idle", timeout=20
        )
        after = _token_expiry_by_hash(job_db, token_hash_row)
        # Only the tool_call keepalive ran with an aging token: the expiry
        # must have been slid forward toward a full TTL.
        assert after > before
        assert not _invalidation_messages(service, session["id"])
    finally:
        service.shutdown()


def _sole_live_token_hash(job_db) -> str:
    with job_db.connect() as conn:
        row = conn.execute(
            "select token_hash from auth_scoped_tokens where revoked_at is null"
        ).fetchone()
    assert row is not None
    return str(row["token_hash"])


def _token_expiry_by_hash(job_db, token_hash: str) -> datetime:
    with job_db.connect() as conn:
        row = conn.execute(
            "select expires_at from auth_scoped_tokens where token_hash=%s", (token_hash,)
        ).fetchone()
    assert row is not None
    return row["expires_at"]

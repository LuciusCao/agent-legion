"""Send-path seamless resume (#1121): a dead run token auto-resumes before admission.

An idle session whose run token died (TTL / revoke) used to refuse the next
send with 409 + 「会话已中断」 AFTER the client had cleared the input — the
message was lost. send_message now checks token liveness up front: dead on an
idle/error row rebuilds the runtime (fresh token, respawned agent) through the
same claim/teardown/spawn machine as the manual resume, and only then admits
the message. These tests pin: delivery after auto-resume, resume-once under
concurrency, failure leaves nothing persisted, and the live-token fast path
never touches the resume machinery.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from server.app.auth.scoped_tokens import authenticate_scoped_token
from server.app.auth.sessions import hash_token
from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.studio_chat import resume as resume_module
from server.app.studio_chat import send_resume
from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.resume_context import RESUME_TRANSCRIPT_HEADER
from tests.helpers import studio_chat_fixtures, wait_for_predicate
from tests.postgres_support import TEST_DATABASE_URL

TEXT_SCRIPT = studio_chat_fixtures.TEXT_SCRIPT
chat = studio_chat_fixtures.chat

WAIT_FOR_CANCEL_SCRIPT = {**TEXT_SCRIPT, "wait_for_cancel": True}


def _wait_for(condition, timeout: float = 20.0, interval: float = 0.05) -> None:
    wait_for_predicate(condition, timeout=timeout, interval=interval)


def _read_sink(script_path: Path) -> list[dict]:
    sink = Path(str(script_path) + ".sink.jsonl")
    if not sink.exists():
        return []
    return [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines()]


def _requests(sink: list[dict], method: str) -> list[dict]:
    return [
        entry["received"] for entry in sink if entry.get("received", {}).get("method") == method
    ]


def _prompt_texts(sink: list[dict]) -> list[str]:
    texts = []
    for request in _requests(sink, "session/prompt"):
        texts.extend(block.get("text", "") for block in request["params"].get("prompt", []))
    return texts


def _idle(service, session_id: str) -> bool:
    return service.get_session(session_id)["status"] == "idle"


def _expire_token(db, token: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "update auth_scoped_tokens set expires_at = current_timestamp"
            " - interval '1 minute' where token_hash=%s",
            (hash_token(token),),
        )


def _user_texts(service, session_id: str, workspace_id: str) -> list[str]:
    return [
        m["content"]["text"]
        for m in service.list_messages(session_id, workspace_id)
        if m["kind"] == "text" and m["role"] == "user"
    ]


def test_dead_token_send_auto_resumes_and_delivers(chat) -> None:
    """Core #1121 flow: idle session, expired token — the send rebuilds the
    runtime (fresh token, fresh process) and the message is answered, with the
    persisted transcript restoring context (loadSession not advertised)."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    sid = session["id"]
    service.send_message(sid, workspace_id, "first question")
    _wait_for(lambda: _idle(service, sid))
    old_runtime = service.runtime(sid)
    assert old_runtime is not None
    _expire_token(service.db, old_runtime.token)
    assert authenticate_scoped_token(service.db, old_runtime.token) is None

    message = service.send_message(sid, workspace_id, "second question")
    assert message["content"]["text"] == "second question"
    _wait_for(lambda: _idle(service, sid))

    # A NEW runtime with a NEW live token; the old token was never revived.
    new_runtime = service.runtime(sid)
    assert new_runtime is not None and new_runtime is not old_runtime
    assert new_runtime.token != old_runtime.token
    assert authenticate_scoped_token(service.db, new_runtime.token) is not None
    assert authenticate_scoped_token(service.db, old_runtime.token) is None
    # 无显式发送者（service 级调用）：新 token 回落到会话创建者身份。
    record = service.db.get_scoped_token_user(hash_token(new_runtime.token))
    assert record is not None and str(record["id"]) == user_id
    assert record["scoped_workspace_id"] == workspace_id
    # The message was admitted once, onto the fresh turn, and answered.
    assert _user_texts(service, sid, workspace_id) == ["first question", "second question"]
    sink = _read_sink(script_path)
    assert len(_requests(sink, "initialize")) == 2  # exactly one respawn
    prompts = _prompt_texts(sink)
    assert len(prompts) == 2
    # Context rebuild: the second prompt carries the transcript and ends with
    # the new message (it is this turn's prompt, not a replay).
    assert RESUME_TRANSCRIPT_HEADER in prompts[1]
    assert "用户：first question" in prompts[1]
    assert prompts[1].endswith("second question")
    # Seamless: no dead-token warning on the timeline — session_resumed is
    # the trace that the channel was rebuilt.
    events = [
        m["content"].get("event")
        for m in service.list_messages(sid, workspace_id)
        if m["kind"] == "status"
    ]
    assert "run_token_invalidated" not in events
    assert "session_resumed" in events


def test_auto_resume_mints_token_for_the_current_sender(chat) -> None:
    """#1121 codex P2-1: the creator got disabled (which is WHY the old token
    reads dead — the liveness join filters disabled users); the auto-resume
    must mint the fresh token for the CURRENT sender, or the new token is born
    dead and every send respawn-loops with no manual entry. Workspace binding
    of the token is unchanged (the session's workspace, only the user swaps)."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    sid = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    service.send_message(sid, workspace_id, "first question")
    _wait_for(lambda: _idle(service, sid))
    service.db.update_user(user_id, disabled=True)
    old_runtime = service.runtime(sid)
    assert old_runtime is not None
    assert not send_resume.run_token_alive(service, old_runtime)
    editor_id = str(service.db.create_user("editor-user", password_hash=None)["id"])

    message = service.send_message(sid, workspace_id, "from the editor", user_id=editor_id)

    assert message["content"]["text"] == "from the editor"
    _wait_for(lambda: _idle(service, sid))
    new_runtime = service.runtime(sid)
    assert new_runtime is not None and new_runtime is not old_runtime
    record = service.db.get_scoped_token_user(hash_token(new_runtime.token))
    assert record is not None and str(record["id"]) == editor_id
    assert record["scoped_workspace_id"] == workspace_id
    assert len(_requests(_read_sink(script_path), "initialize")) == 2
    assert _user_texts(service, sid, workspace_id) == ["first question", "from the editor"]


def test_concurrent_sends_on_dead_token_resume_once(chat, monkeypatch) -> None:
    """Two sends racing a dead token: the loser blocks on the per-session
    resume lock and rides the winner's fresh runtime — exactly one respawn,
    both messages admitted (the loser's after the winner's turn settles)."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    sid = session["id"]
    runtime = service.runtime(sid)
    assert runtime is not None
    _expire_token(service.db, runtime.token)

    winner_in_resume = threading.Event()
    loser_at_lock = threading.Event()
    winner_turn_done = threading.Event()
    winner_holder: list[threading.Thread] = []
    loser_holder: list[threading.Thread] = []

    real_find_agent = service._registry.find_agent

    def find_agent(agent_id: str):
        # The resume winner parks after the lock, BEFORE the claim: the loser
        # still reads idle + dead token and queues up behind the lock.
        if winner_holder and threading.current_thread() is winner_holder[0]:
            winner_in_resume.set()
            assert loser_at_lock.wait(timeout=30)
        return real_find_agent(agent_id)

    class _LoserSignalingLock:
        def __init__(self, real: threading.Lock) -> None:
            self._real = real

        def __enter__(self):
            loser_at_lock.set()
            # Ride means "after the winner's turn settled", so the loser's own
            # admission finds the row idle (a human turn in flight is a
            # pre-existing 409-busy, unrelated to resume-once).
            assert winner_turn_done.wait(timeout=30)
            self._real.acquire()
            return self

        def __exit__(self, *args) -> None:
            self._real.release()

    real_lock = send_resume._resume_lock

    def resume_lock(service_arg, session_id: str):
        real = real_lock(service_arg, session_id)
        if loser_holder and threading.current_thread() is loser_holder[0]:
            return _LoserSignalingLock(real)
        return real

    spawn_calls = {"n": 0}
    real_spawn = resume_module.spawn_session_runtime

    def counting_spawn(*args, **kwargs):
        spawn_calls["n"] += 1
        return real_spawn(*args, **kwargs)

    monkeypatch.setattr(service._registry, "find_agent", find_agent)
    monkeypatch.setattr(send_resume, "_resume_lock", resume_lock)
    monkeypatch.setattr(resume_module, "spawn_session_runtime", counting_spawn)

    results: dict[str, object] = {}
    errors: dict[str, Exception] = {}

    def send(name: str, text: str) -> None:
        try:
            results[name] = service.send_message(sid, workspace_id, text)
        except Exception as exc:  # noqa: BLE001 - assertions live on the main thread
            errors[name] = exc

    winner = threading.Thread(target=send, args=("winner", "winner message"))
    winner_holder.append(winner)
    winner.start()
    assert winner_in_resume.wait(timeout=30)
    loser = threading.Thread(target=send, args=("loser", "loser message"))
    loser_holder.append(loser)
    loser.start()
    # Release the loser only once the winner's post-resume turn has fully
    # settled (signal, not duration).
    _wait_for(
        lambda: (
            len(_requests(_read_sink(script_path), "initialize")) == 2
            and len(_prompt_texts(_read_sink(script_path))) == 1
            and _idle(service, sid)
        )
    )
    winner_turn_done.set()
    winner.join(timeout=30)
    loser.join(timeout=30)
    assert not winner.is_alive() and not loser.is_alive()

    assert errors == {}
    assert results["winner"]["content"]["text"] == "winner message"
    assert results["loser"]["content"]["text"] == "loser message"
    assert spawn_calls["n"] == 1  # resume ran exactly once
    _wait_for(lambda: _idle(service, sid) and len(_prompt_texts(_read_sink(script_path))) == 2)
    assert _user_texts(service, sid, workspace_id) == ["winner message", "loser message"]


def test_concurrent_send_waits_out_the_registration_window(chat, monkeypatch) -> None:
    """#1121 codex P2-2 (round 3): a send arriving while the winner's
    auto-resume sits in the registration window (old runtime torn down, new
    one not yet registered, row starting) must not hit the orphan 409 nor
    spawn a second resume — it waits out the per-session lock and rides the
    freshly registered runtime."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    sid = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    runtime = service.runtime(sid)
    assert runtime is not None
    _expire_token(service.db, runtime.token)

    winner_in_spawn = threading.Event()
    loser_at_lock = threading.Event()
    winner_turn_done = threading.Event()
    loser_holder: list[threading.Thread] = []

    spawn_calls = {"n": 0}
    real_spawn = resume_module.spawn_session_runtime

    def parking_spawn(*args, **kwargs):
        # The registration window: claim done (starting), teardown done
        # (runtime registry empty) — the loser arrives exactly here.
        spawn_calls["n"] += 1
        winner_in_spawn.set()
        assert loser_at_lock.wait(timeout=30)
        return real_spawn(*args, **kwargs)

    class _LoserSignalingLock:
        def __init__(self, real: threading.Lock) -> None:
            self._real = real

        def __enter__(self):
            loser_at_lock.set()
            # Ride = the winner's turn has settled (a human turn in flight is
            # a pre-existing 409-busy, unrelated to the window fix).
            assert winner_turn_done.wait(timeout=30)
            self._real.acquire()
            return self

        def __exit__(self, *args) -> None:
            self._real.release()

    real_lock = send_resume._resume_lock

    def resume_lock(service_arg, session_id: str):
        real = real_lock(service_arg, session_id)
        if loser_holder and threading.current_thread() is loser_holder[0]:
            return _LoserSignalingLock(real)
        return real

    monkeypatch.setattr(resume_module, "spawn_session_runtime", parking_spawn)
    monkeypatch.setattr(send_resume, "_resume_lock", resume_lock)

    results: dict[str, object] = {}
    errors: dict[str, Exception] = {}

    def send(name: str, text: str) -> None:
        try:
            results[name] = service.send_message(sid, workspace_id, text)
        except Exception as exc:  # noqa: BLE001 - assertions live on the main thread
            errors[name] = exc

    winner = threading.Thread(target=send, args=("winner", "winner message"))
    winner.start()
    assert winner_in_spawn.wait(timeout=30)
    # 注册窗口确认：runtime 已摘除、行是 starting——败者此刻进入。
    assert service.runtime(sid) is None
    assert service.get_session(sid)["status"] == "starting"
    loser = threading.Thread(target=send, args=("loser", "loser message"))
    loser_holder.append(loser)
    loser.start()
    # 等胜者的首轮落定（信号，非时长）再放行败者过锁。
    _wait_for(lambda: len(_prompt_texts(_read_sink(script_path))) == 1 and _idle(service, sid))
    winner_turn_done.set()
    winner.join(timeout=30)
    loser.join(timeout=30)
    assert not winner.is_alive() and not loser.is_alive()

    assert errors == {}
    assert spawn_calls["n"] == 1  # 无二次 spawn
    assert results["winner"]["content"]["text"] == "winner message"
    assert results["loser"]["content"]["text"] == "loser message"
    _wait_for(lambda: _idle(service, sid) and len(_prompt_texts(_read_sink(script_path))) == 2)
    assert _user_texts(service, sid, workspace_id) == ["winner message", "loser message"]


def test_auto_resume_preflight_failure_leaves_the_session_untouched(chat) -> None:
    """#1121 codex P2-1 (round 3): an agent that cannot be spawned (command
    gone) fails the resume PREFLIGHT — before any state write. The session
    keeps its truthful idle row (the old runtime is still alive), the message
    is not persisted, and fixing the registry + resending IS the manual
    recovery entry (no misleading error stamp to resume out of)."""
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    sid = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    service.send_message(sid, workspace_id, "first question")
    _wait_for(lambda: _idle(service, sid))
    runtime = service.runtime(sid)
    assert runtime is not None
    _expire_token(service.db, runtime.token)
    StudioAgentRegistryStore(TEST_DATABASE_URL).put(
        {
            "api_base": "http://127.0.0.1:8000",
            "agents": [
                {
                    "id": "fake-agent",
                    "label": "Fake Agent",
                    "command": "/nonexistent/fake-agent-binary",
                    "args": [],
                }
            ],
        }
    )

    with pytest.raises(InvalidOperationError, match="not available"):
        service.send_message(sid, workspace_id, "never answered")

    assert _user_texts(service, sid, workspace_id) == ["first question"]
    session = service.get_session(sid)
    assert session["status"] == "idle"  # 不被无谓写成 error：旧 runtime 还活着
    assert not session["error_detail"]
    assert service.runtime(sid) is runtime  # 旧 runtime 未被 teardown

    # 手动入口 = 修好注册表后重发（自动路径重试即恢复）。
    register(TEXT_SCRIPT)
    message = service.send_message(sid, workspace_id, "never answered")
    assert message["content"]["text"] == "never answered"
    _wait_for(lambda: _idle(service, sid))
    assert _user_texts(service, sid, workspace_id) == ["first question", "never answered"]


def test_live_token_send_never_touches_the_resume_path(chat, monkeypatch) -> None:
    """Regression: a healthy session pays no resume machinery — no claim, no
    respawn, the same token object keeps riding the runtime."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    sid = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    runtime = service.runtime(sid)
    assert runtime is not None
    claims = {"n": 0}
    real_claim = service.db.claim_studio_chat_resume

    def counting_claim(*args, **kwargs):
        claims["n"] += 1
        return real_claim(*args, **kwargs)

    monkeypatch.setattr(service.db, "claim_studio_chat_resume", counting_claim)
    service.send_message(sid, workspace_id, "hello")
    _wait_for(lambda: _idle(service, sid))

    assert claims["n"] == 0
    assert len(_requests(_read_sink(script_path), "initialize")) == 1
    assert service.runtime(sid) is runtime


def test_running_turn_stays_fail_closed_then_next_send_auto_resumes(chat) -> None:
    """Mid-turn token death is NOT auto-resumed (an in-flight turn keeps the
    existing escalate + 409 path — ripping it down would lose partial work);
    the NEXT send then auto-resumes from the escalated error row."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(WAIT_FOR_CANCEL_SCRIPT)
    sid = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    service.send_message(sid, workspace_id, "first question")
    _wait_for(lambda: service.get_session(sid)["status"] == "running")
    runtime = service.runtime(sid)
    assert runtime is not None
    _expire_token(service.db, runtime.token)

    with pytest.raises(ConflictError, match="继续对话"):
        service.send_message(sid, workspace_id, "during the doomed turn")
    assert _user_texts(service, sid, workspace_id) == ["first question"]
    _wait_for(lambda: service.get_session(sid)["status"] == "error")

    # The resumed process re-reads the script: drop wait_for_cancel so the
    # post-resume turn can end on its own (the first process held the turn).
    script_path.write_text(json.dumps(TEXT_SCRIPT), encoding="utf-8")
    # The session is now error + dead token: the next send auto-resumes.
    message = service.send_message(sid, workspace_id, "after recovery")
    assert message["content"]["text"] == "after recovery"
    _wait_for(lambda: _idle(service, sid))
    assert _user_texts(service, sid, workspace_id) == ["first question", "after recovery"]
    assert len(_requests(_read_sink(script_path), "initialize")) == 2


def test_auto_resume_aborts_when_close_wins_the_cas_race(chat, monkeypatch) -> None:
    """#1121 codex P1: a close landing between the snapshot gate and the CAS
    claim must abort the auto-resume — the guarded idle→error stamp matches
    zero rows, nothing resumes, the just-closed session stays closed."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    sid = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    runtime = service.runtime(sid)
    assert runtime is not None
    _expire_token(service.db, runtime.token)
    real_update = service.db.update_studio_chat_session_if

    def close_then_cas(session_id, **kwargs):
        # The CAS stamp is the only idle→error guarded write on this path.
        if kwargs.get("status") == "error" and kwargs.get("status_in") == ("idle",):
            service.close_session(sid, workspace_id)
        return real_update(session_id, **kwargs)

    monkeypatch.setattr(service.db, "update_studio_chat_session_if", close_then_cas)
    with pytest.raises(ConflictError, match="closed"):
        service.send_message(sid, workspace_id, "must not reopen me")

    session = service.get_session(sid)
    assert session["status"] == "closed"
    assert session["closed_at"] is not None  # 仍是 close 的关闭，未被 resume 清掉
    assert _user_texts(service, sid, workspace_id) == []
    assert len(_requests(_read_sink(script_path), "initialize")) == 1  # no respawn


def test_auto_resume_claim_refuses_a_close_landing_after_the_cas(chat, monkeypatch) -> None:
    """#1121 codex P1, second window: the CAS succeeded (row error) but the
    close lands before the resume claim — allow_closed=False makes the claim
    fail and the session stays closed (manual resume keeps accepting closed)."""
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    sid = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    runtime = service.runtime(sid)
    assert runtime is not None
    _expire_token(service.db, runtime.token)
    real_claim = service.db.claim_studio_chat_resume

    def close_then_claim(session_id, **kwargs):
        # The auto path must never claim a closed row (manual resume keeps
        # the default allow_closed=True — not exercised here).
        assert kwargs.get("allow_closed") is False
        service.close_session(sid, workspace_id)
        return real_claim(session_id, **kwargs)

    monkeypatch.setattr(service.db, "claim_studio_chat_resume", close_then_claim)
    with pytest.raises(ConflictError, match="closed"):
        service.send_message(sid, workspace_id, "must not reopen me")

    session = service.get_session(sid)
    assert session["status"] == "closed"
    assert session["closed_at"] is not None
    assert _user_texts(service, sid, workspace_id) == []
    assert len(_requests(_read_sink(script_path), "initialize")) == 1  # no respawn

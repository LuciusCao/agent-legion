"""Studio chat session archive at the service layer (#924).

Runtime semantics match close: archiving a live session closes it and
revokes the run token; an archived session cannot be resumed (409) until it
is unarchived, and unarchive never spawns a runtime. An archive that lands
between a resume's claim and its runtime registration must be caught by the
spawn registration fence (the #872 delete fence, extended to archived_at).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.auth.scoped_tokens import authenticate_scoped_token
from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.studio_chat import resume as resume_module
from server.app.studio_chat import spawn as spawn_module
from tests.helpers import studio_chat_fixtures

TEXT_SCRIPT = studio_chat_fixtures.TEXT_SCRIPT
chat = studio_chat_fixtures.chat


def _session_new_count(script_path: Path) -> int:
    sink = Path(str(script_path) + ".sink.jsonl")
    if not sink.exists():
        return 0
    return sum(
        1
        for line in sink.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("received", {}).get("method") == "session/new"
    )


def _capture_mints(monkeypatch) -> list[str]:
    minted: list[str] = []
    original_mint = spawn_module.mint_scoped_token

    def capture_mint(*args, **kwargs):
        token = original_mint(*args, **kwargs)
        minted.append(token)
        return token

    monkeypatch.setattr(spawn_module, "mint_scoped_token", capture_mint)
    return minted


def test_archive_live_session_closes_runtime_and_blocks_resume(chat, job_db, monkeypatch) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    minted = _capture_mints(monkeypatch)
    session_id = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    assert service.runtime(session_id) is not None

    archived = service.archive_session(session_id, workspace_id)
    assert archived["status"] == "closed"
    assert archived["archived_at"] is not None
    assert service.runtime(session_id) is None
    assert authenticate_scoped_token(job_db, minted[0]) is None
    # Idempotent: archiving again answers the same row.
    assert service.archive_session(session_id, workspace_id)["archived_at"] is not None

    spawned = _session_new_count(script_path)
    with pytest.raises(ConflictError, match="archived"):
        service.resume_session(session_id, workspace_id, user_id)
    assert _session_new_count(script_path) == spawned

    restored = service.unarchive_session(session_id, workspace_id)
    assert restored["archived_at"] is None
    # Unarchive never spawns: the row stays closed with no runtime ...
    assert restored["status"] == "closed"
    assert service.runtime(session_id) is None
    assert _session_new_count(script_path) == spawned
    # ... and the existing resume path brings it back.
    assert service.resume_session(session_id, workspace_id, user_id)["status"] == "idle"
    service.close_session(session_id, workspace_id)


def test_archive_between_resume_claim_and_registration_leaves_no_runtime(
    chat, job_db, monkeypatch
) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session_id = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    service.close_session(session_id, workspace_id)
    spawned_before = _session_new_count(script_path)
    minted = _capture_mints(monkeypatch)
    original_spawn = resume_module.spawn_session_runtime

    def archive_then_spawn(*args, **kwargs):
        # The resume already claimed (closed -> starting); the archive lands
        # before the runtime registers, finds nothing to retire, and closes.
        service.archive_session(session_id, workspace_id)
        assert service.runtime(session_id) is None
        return original_spawn(*args, **kwargs)

    monkeypatch.setattr(resume_module, "spawn_session_runtime", archive_then_spawn)

    with pytest.raises(InvalidOperationError, match="archived"):
        service.resume_session(session_id, workspace_id, user_id)

    assert service.runtime(session_id) is None
    row = job_db.get_studio_chat_session(session_id)
    assert row is not None
    assert row["status"] == "closed"
    assert row["archived_at"] is not None
    assert len(minted) == 1
    assert authenticate_scoped_token(job_db, minted[0]) is None
    assert _session_new_count(script_path) == spawned_before

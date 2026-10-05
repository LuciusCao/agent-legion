"""Studio chat soft delete vs. an in-flight resume (#872 review R1 P1).

A delete landing after a resume claimed the row (and re-read it) but before
spawn_session_runtime registered the runtime finds nothing to retire and
answers at once; the registration point must then refuse to register or
start a runtime for the closed/deleted row and revoke the minted run token.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.auth.scoped_tokens import authenticate_scoped_token
from server.app.services.job_errors import InvalidOperationError
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


def test_delete_between_resume_claim_and_registration_leaves_no_runtime(
    chat, job_db, monkeypatch
) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    session_id = session["id"]
    service.close_session(session_id, workspace_id)
    spawned_before = _session_new_count(script_path)

    minted: list[str] = []
    original_mint = spawn_module.mint_scoped_token

    def capture_mint(*args, **kwargs):
        token = original_mint(*args, **kwargs)
        minted.append(token)
        return token

    monkeypatch.setattr(spawn_module, "mint_scoped_token", capture_mint)
    original_spawn = resume_module.spawn_session_runtime

    def delete_then_spawn(*args, **kwargs):
        # The resume already claimed (closed -> starting) and re-read the row;
        # the delete lands before the runtime is registered: no runtime to
        # retire, so it closes the row and answers success immediately.
        service.delete_session(session_id, workspace_id)
        assert service.runtime(session_id) is None
        return original_spawn(*args, **kwargs)

    monkeypatch.setattr(resume_module, "spawn_session_runtime", delete_then_spawn)

    with pytest.raises(InvalidOperationError, match="closed, deleted or archived"):
        service.resume_session(session_id, workspace_id, user_id)

    assert service.runtime(session_id) is None
    row = job_db.get_studio_chat_session(session_id)
    assert row is not None
    assert row["status"] == "closed"
    assert row["deleted_at"] is not None
    # The run token minted for the refused spawn is revoked ...
    assert len(minted) == 1
    assert authenticate_scoped_token(job_db, minted[0]) is None
    # ... and no agent subprocess was ever started for it.
    assert _session_new_count(script_path) == spawned_before

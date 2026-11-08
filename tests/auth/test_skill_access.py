"""Adversarial ownership matrix for the shared skill auth helpers (#993)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.exceptions import HTTPException

from server.app.auth.skill_access import (
    require_skill_key_in_workspace,
    require_skill_path_in_workspace,
    require_skill_writable_in_workspace,
    resolve_skill_key_owner,
)
from server.app.services.job_errors import NotFoundError

pytestmark = pytest.mark.no_db


def _db() -> Mock:
    db = Mock()
    db.get_workspace.side_effect = lambda workspace_id: (
        {"id": workspace_id} if workspace_id == "ws_home" else None
    )
    db.list_workspaces.return_value = [{"id": "ws_home"}]
    return db


def test_workspace_and_group_key_matrix() -> None:
    db = _db()
    assert resolve_skill_key_owner(db, "ws_home/private") == "ws_home"
    assert resolve_skill_key_owner(db, "shared-group/public") is None
    require_skill_key_in_workspace(db, "ws_home/private", "ws_home")
    require_skill_key_in_workspace(db, "shared-group/public", "ws_other")

    with pytest.raises(HTTPException) as refused:
        require_skill_key_in_workspace(db, "ws_home/private", "ws_other")
    assert (refused.value.status_code, refused.value.detail) == (404, "Skill not found")


def test_case_variant_is_not_a_group_and_each_refusal_is_fresh() -> None:
    db = _db()
    with pytest.raises(HTTPException) as first:
        resolve_skill_key_owner(db, "WS_Home/private")
    with pytest.raises(HTTPException) as second:
        require_skill_key_in_workspace(db, "Ws_Home/private", "ws_other")
    assert first.value is not second.value
    assert (first.value.status_code, first.value.detail) == (404, "Skill not found")
    assert (second.value.status_code, second.value.detail) == (404, "Skill not found")


def test_group_and_foreign_workspace_keys_are_not_studio_writable() -> None:
    db = _db()
    require_skill_writable_in_workspace(db, "ws_home/private", "ws_home")
    with pytest.raises(NotFoundError, match="Group skills are read-only for studio agents"):
        require_skill_writable_in_workspace(db, "shared-group/public", "ws_home")
    with pytest.raises(NotFoundError, match="Skill not found in workspace ws_other"):
        require_skill_writable_in_workspace(db, "ws_home/private", "ws_other")


def test_absolute_path_guard_only_extracts_complete_keys_inside_base(tmp_path) -> None:
    db = _db()
    base = tmp_path / "skills"
    require_skill_path_in_workspace(db, str(base / "ws_home" / "private"), base, "ws_home")
    require_skill_path_in_workspace(db, str(base / "shared" / "public"), base, "ws_other")

    with pytest.raises(HTTPException) as refused:
        require_skill_path_in_workspace(db, str(base / "ws_home" / "private"), base, "ws_other")
    assert (refused.value.status_code, refused.value.detail) == (404, "Skill not found")

    for path in (str(base), str(base / "ws_home"), str(tmp_path / "outside"), "relative", "\0"):
        require_skill_path_in_workspace(db, path, base, "ws_other")

"""Route-specific refusal shapes after sharing workspace role checks (#993)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.exceptions import HTTPException
from starlette.requests import Request

from server.app.auth.workspace_access import require_job_workspace_access, require_workspace_access

pytestmark = pytest.mark.no_db


def _request(
    db: Mock,
    *,
    method: str = "GET",
    path_params: dict[str, str] | None = None,
    query: bytes = b"",
) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(job_db=db))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/probe",
            "path_params": path_params or {},
            "query_string": query,
            "headers": [],
            "app": app,
        }
    )


def _admin(binding: str | None = None) -> dict:
    return {
        "id": "admin-id",
        "role": "admin",
        "actor_scope": "studio_agent" if binding else None,
        "scoped_workspace_id": binding,
    }


def test_admin_minted_binding_precedes_generic_admin_fast_path() -> None:
    db = Mock()
    request = _request(db, query=b"workspace_id=ws_foreign")
    with pytest.raises(HTTPException) as refused:
        require_workspace_access(request, _admin("ws_home"))
    assert (refused.value.status_code, refused.value.detail) == (
        403,
        "Scoped token bound to another workspace",
    )
    db.get_workspace_role.assert_not_called()


def test_admin_minted_binding_is_a_job_not_found_on_job_routes() -> None:
    db = Mock()
    db.get_job_workspace.return_value = "ws_foreign"
    request = _request(db, path_params={"job_id": "job-1"})
    with pytest.raises(HTTPException) as refused:
        require_job_workspace_access(request, _admin("ws_home"))
    assert (refused.value.status_code, refused.value.detail) == (404, "Job not found")
    db.get_workspace_role.assert_not_called()


def test_job_and_path_workspace_must_agree_even_for_admin() -> None:
    db = Mock()
    db.get_job_workspace.return_value = "ws_foreign"
    request = _request(db, path_params={"job_id": "job-1", "workspace_id": "ws_home"})
    with pytest.raises(HTTPException) as refused:
        require_job_workspace_access(request, _admin())
    assert (refused.value.status_code, refused.value.detail) == (404, "Job not found")


def test_malformed_job_id_is_rejected_before_job_lookup() -> None:
    db = Mock()
    request = _request(db, path_params={"job_id": "bad\0id"})
    with pytest.raises(HTTPException) as refused:
        require_job_workspace_access(request, _admin())
    assert (refused.value.status_code, refused.value.detail) == (400, "Invalid job id")
    db.get_job_workspace.assert_not_called()


def test_generic_and_job_membership_refusals_keep_their_details() -> None:
    db = Mock()
    db.get_workspace_role.return_value = None
    db.get_job_workspace.return_value = "ws_foreign"
    user = {"id": "user-id", "role": "user"}

    with pytest.raises(HTTPException) as workspace:
        require_workspace_access(_request(db, query=b"workspace_id=ws_foreign"), user)
    with pytest.raises(HTTPException) as job:
        require_job_workspace_access(_request(db, path_params={"job_id": "job-1"}), user)

    assert (workspace.value.status_code, workspace.value.detail) == (404, "Workspace not found")
    assert (job.value.status_code, job.value.detail) == (404, "Job not found")

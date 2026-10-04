"""App-level ``JobServiceError`` handler (#927).

The per-route ``except JobServiceError: raise_job_http_error(exc)``
boilerplate was replaced by one app-level handler. These standalone-app
tests pin that the handler is a drop-in: for every ``JobServiceError``
subclass, a route that lets the error escape answers byte-identically
(status, JSON body, headers) to a route that still runs the legacy
route-level mapping, and the explicit status table matches the documented
mapping. No database is touched.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.routes.job_http import raise_job_http_error
from server.app.routes.job_http_handlers import register_job_http_exception_handlers
from server.app.services.job_errors import (
    ConflictError,
    CustomNodesDisabledError,
    DraftConflictError,
    DraftWorkflowKeyMismatchError,
    InvalidDraftCasTokenError,
    InvalidOperationError,
    JobServiceError,
    NotFoundError,
    UnsupportedOperationError,
)
from server.app.services.job_log_raw import PayloadTooLargeError
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_selection_resolver import BatchSelectionTooLargeError
from server.app.services.material_bundles import BundleInUseError
from server.app.services.materials import (
    MaterialInUseError,
    MaterialStorageUnavailableError,
    MaterialVerificationError,
)
from server.app.services.run_partial_failure import PartialRunCreationError
from server.app.services.skill_repo import SkillGitError
from server.app.services.skill_repo_edit import SkillEditValidationError, SkillRollbackError
from server.app.services.skill_shared_store import SharedMaterialWriteError

pytestmark = pytest.mark.no_db

_DRAFT_CONFLICT_PAYLOAD = {
    "message": "draft changed",
    "expected_updated_at": "2026-01-01T00:00:00+00:00",
    "current_updated_at": "2026-01-02T00:00:00+00:00",
    "draft": {"nodes": []},
}

# (case id, error factory, expected status, expected detail)
_MAPPED_CASES = [
    ("not_found", lambda: NotFoundError("Job not found"), 404, "Job not found"),
    (
        "custom_nodes_disabled",
        lambda: CustomNodesDisabledError("custom nodes disabled"),
        403,
        "custom nodes disabled",
    ),
    ("conflict", lambda: ConflictError("busy"), 409, "busy"),
    ("conflict_subclass", lambda: BundleInUseError("bundle in use"), 409, "bundle in use"),
    (
        "draft_conflict_payload",
        lambda: DraftConflictError(dict(_DRAFT_CONFLICT_PAYLOAD)),
        409,
        _DRAFT_CONFLICT_PAYLOAD,
    ),
    ("unsupported", lambda: UnsupportedOperationError("nope"), 501, "nope"),
    ("payload_too_large", lambda: PayloadTooLargeError("too big"), 413, "too big"),
    (
        "draft_key_mismatch",
        lambda: DraftWorkflowKeyMismatchError("key mismatch"),
        422,
        "key mismatch",
    ),
    ("invalid_cas_token", lambda: InvalidDraftCasTokenError("bad token"), 422, "bad token"),
    (
        "partial_run_creation",
        lambda: PartialRunCreationError("chunk 2 failed", run_id="run-1", created_so_far=3),
        400,
        {"message": "chunk 2 failed", "run_id": "run-1", "created_so_far": 3},
    ),
    (
        "skill_edit_validation",
        lambda: SkillEditValidationError("invalid", [{"path": "a", "error": "bad"}]),
        422,
        {"message": "invalid", "errors": [{"path": "a", "error": "bad"}]},
    ),
    ("invalid_operation", lambda: InvalidOperationError("bad input"), 400, "bad input"),
]

# Subclasses the shared mapping does not know: the legacy route-level call
# re-raised them (-> generic 500) and the app-level handler must too. The
# material errors keep their bespoke 503/422/409 only inside routes that
# still catch them locally (materials, runs), never globally.
_UNMAPPED_CASES = [
    ("base", lambda: JobServiceError("boom")),
    ("job_operation", lambda: JobOperationError("job-1", "rerun", "failed", reason_code="x")),
    ("skill_git", lambda: SkillGitError("git failed")),
    ("skill_rollback", lambda: SkillRollbackError("rollback failed")),
    ("shared_material_write", lambda: SharedMaterialWriteError("io failed")),
    ("material_storage", lambda: MaterialStorageUnavailableError("storage down")),
    ("material_verification", lambda: MaterialVerificationError("bad object")),
    ("material_in_use", lambda: MaterialInUseError("in use")),
]

_ALL_CASES = {case_id: factory for case_id, factory, *_ in _MAPPED_CASES + _UNMAPPED_CASES}


def _build_app() -> FastAPI:
    app = FastAPI()
    register_job_http_exception_handlers(app)

    @app.get("/handler/{case_id}")
    def escape_to_handler(case_id: str) -> None:
        raise _ALL_CASES[case_id]()

    @app.get("/async-handler/{case_id}")
    async def escape_to_handler_async(case_id: str) -> None:
        raise _ALL_CASES[case_id]()

    @app.get("/legacy/{case_id}")
    def legacy_route_mapping(case_id: str) -> None:
        try:
            raise _ALL_CASES[case_id]()
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @app.get("/batch-too-large")
    def batch_too_large() -> None:
        raise BatchSelectionTooLargeError(1000)

    return app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(_build_app(), raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("case_id", "expected_status", "expected_detail"),
    [(case_id, status, detail) for case_id, _f, status, detail in _MAPPED_CASES],
    ids=[case_id for case_id, *_ in _MAPPED_CASES],
)
def test_handler_maps_each_subclass(client, case_id, expected_status, expected_detail):
    for prefix in ("/handler", "/async-handler"):
        resp = client.get(f"{prefix}/{case_id}")
        assert resp.status_code == expected_status
        assert resp.json() == {"detail": expected_detail}


@pytest.mark.parametrize("case_id", sorted(_ALL_CASES))
def test_handler_response_identical_to_legacy_route_mapping(client, case_id):
    legacy = client.get(f"/legacy/{case_id}")
    via_handler = client.get(f"/handler/{case_id}")
    assert via_handler.status_code == legacy.status_code
    assert via_handler.content == legacy.content
    assert dict(via_handler.headers) == dict(legacy.headers)


@pytest.mark.parametrize("case_id", [case_id for case_id, _f in _UNMAPPED_CASES])
def test_unmapped_subclass_still_surfaces_as_500(client, case_id):
    assert client.get(f"/handler/{case_id}").status_code == 500


def test_unmapped_subclass_propagates_unchanged():
    strict = TestClient(_build_app())
    with pytest.raises(SkillGitError, match="git failed"):
        strict.get("/handler/skill_git")


def test_batch_selection_handler_unaffected(client):
    resp = client.get("/batch-too-large")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == BatchSelectionTooLargeError(1000).code


def test_register_installs_job_service_error_handler():
    app = FastAPI()
    register_job_http_exception_handlers(app)
    assert JobServiceError in app.exception_handlers
    assert BatchSelectionTooLargeError in app.exception_handlers

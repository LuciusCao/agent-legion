from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from server.app.jobs.queries.workspace_release_manifests import release_manifest_hash
from server.app.services.job_errors import InvalidOperationError
from server.app.services.workspace_release_manifests import (
    WorkspaceReleaseManifestConflictError,
    WorkspaceReleaseManifestsService,
)


@pytest.fixture
def workspace_id(job_db) -> str:
    with job_db.write() as conn:
        conn.execute("insert into workspaces(id, name) values ('release-service', 'Release')")
    return "release-service"


@pytest.fixture
def service(job_db, workspace_id) -> WorkspaceReleaseManifestsService:
    return WorkspaceReleaseManifestsService(job_db)


def test_empty_manifest_add_remove_and_history(service) -> None:
    empty = service.get_current("release-service")
    assert empty["revision"] == 0
    assert empty["artifact_names"] == []
    assert empty["manifest_hash"] == release_manifest_hash([])
    assert empty["created_at"] is None
    assert service.list_revisions("release-service") == []
    assert (
        service.update(
            "release-service", artifact_names=[], expected_revision=0, created_by="editor"
        )
        == empty
    )

    first = service.update(
        "release-service",
        artifact_names=["z.json", "reports/final.json", "z.json"],
        expected_revision=0,
        created_by="editor-1",
    )
    assert first["revision"] == 1
    assert first["artifact_names"] == ["reports/final.json", "z.json"]
    assert first["manifest_hash"] == release_manifest_hash(first["artifact_names"])
    assert first["created_by"] == "editor-1"
    assert first["created_at"] is not None

    second = service.update(
        "release-service",
        artifact_names=["result.json"],
        expected_revision=1,
        created_by="editor-2",
    )
    assert second["revision"] == 2
    assert second["artifact_names"] == ["result.json"]
    assert service.get_current("release-service") == second
    history = service.list_revisions("release-service")
    assert [row["revision"] for row in history] == [2, 1]
    assert history[1] == first

    assert (
        service.update(
            "release-service",
            artifact_names=["result.json", "result.json"],
            expected_revision=2,
            created_by="editor-3",
        )
        == second
    )
    assert len(service.list_revisions("release-service")) == 2


def test_stale_expected_revision_returns_current_without_appending(service) -> None:
    service.update(
        "release-service",
        artifact_names=["one.json"],
        expected_revision=0,
        created_by="editor",
    )
    current = service.update(
        "release-service",
        artifact_names=["two.json"],
        expected_revision=1,
        created_by="editor",
    )
    with pytest.raises(WorkspaceReleaseManifestConflictError) as caught:
        service.update(
            "release-service",
            artifact_names=["stale.json"],
            expected_revision=1,
            created_by="other-editor",
        )
    assert caught.value.payload["expected_revision"] == 1
    assert caught.value.payload["current_manifest"] == current
    assert service.get_current("release-service") == current
    assert len(service.list_revisions("release-service")) == 2


def test_concurrent_expected_revision_admits_exactly_one_append(service) -> None:
    barrier = threading.Barrier(2)

    def update(name: str):
        barrier.wait()
        try:
            return service.update(
                "release-service",
                artifact_names=[name],
                expected_revision=0,
                created_by=name,
            )
        except WorkspaceReleaseManifestConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, ["one.json", "two.json"]))
    appended = [item for item in outcomes if isinstance(item, dict)]
    conflicts = [
        item for item in outcomes if isinstance(item, WorkspaceReleaseManifestConflictError)
    ]
    assert len(appended) == 1
    assert len(conflicts) == 1
    assert appended[0]["revision"] == 1
    assert conflicts[0].payload["current_manifest"] == appended[0]
    assert service.list_revisions("release-service") == appended


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/absolute.json",
        "../escape.json",
        "reports//result.json",
        "./result.json",
        "result.json/",
        "runs/result.json",
        ".hidden/result.json",
        "reports\\result.json",
        "bad\nname.json",
        "x" * 201,
    ],
)
def test_invalid_artifact_names_do_not_append(service, name: str) -> None:
    with pytest.raises(InvalidOperationError, match="Invalid release artifact name"):
        service.update(
            "release-service",
            artifact_names=[name],
            expected_revision=0,
            created_by="editor",
        )
    assert service.get_current("release-service")["revision"] == 0
    assert service.list_revisions("release-service") == []
